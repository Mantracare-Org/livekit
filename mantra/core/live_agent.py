"""Multilingual live agent for the agent worker.

Extracted from the former mantra/agent.py module. The language manager, call
state, TTS engine and voice id are passed through the constructor so the class
is module-scope safe.
"""
import logging

from livekit.agents import Agent, llm
from livekit.agents.voice.agent import ModelSettings

logger = logging.getLogger("mantra.live_agent")


class MantraMultilingualAgent(Agent):
    def __init__(self, *, language_mgr, call_state, tts_engine, voice_id, **kwargs):
        super().__init__(**kwargs)
        self._lm = language_mgr
        self._cs = call_state
        self._tts = tts_engine
        self._voice_id = voice_id

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ):
        # Synchronously align language before LLM generates text
        try:
            msgs = list(chat_ctx.messages()) if callable(getattr(chat_ctx, "messages", None)) else (chat_ctx.messages if isinstance(getattr(chat_ctx, "messages", None), list) else [])
            if msgs:
                user_msgs = [m for m in msgs if hasattr(m, 'role') and str(m.role).lower() in ('user', 'caller')]
                if user_msgs:
                    last_user_msg = user_msgs[-1]
                    content = " ".join([str(c) for c in last_user_msg.content]) if isinstance(last_user_msg.content, list) else str(last_user_msg.content)
                    if content and not content.startswith("[System:"):
                        new_lang, switched = self._lm.process_user_utterance(content)
                        if switched:
                            old_lang = self._cs.get("current_language", "en")
                            self._cs["current_language"] = new_lang
                            logger.info(f"[LANG] Immediate llm_node switch: {old_lang} -> {new_lang}")
                            try:
                                self._tts.update_options(language=new_lang, voice=self._voice_id)
                                logger.info(f"[LANG] TTS updated to language='{new_lang}' (voice={self._voice_id})")
                            except Exception as tts_err:
                                logger.error(f"[LANG] Failed to update TTS options: {tts_err}")
                        # Synchronously update the language directive in the system message inside chat_ctx
                        directive = self._lm.get_prompt_directive()
                        for m in msgs:
                            if hasattr(m, 'role') and str(m.role).lower() in ('system',):
                                sys_text = " ".join([str(c) for c in m.content]) if isinstance(m.content, list) else str(m.content)
                                if "<!-- LANGUAGE_DIRECTIVE_START -->" in sys_text and "<!-- LANGUAGE_DIRECTIVE_END -->" in sys_text:
                                    pref = sys_text.split("<!-- LANGUAGE_DIRECTIVE_START -->")[0]
                                    suff = sys_text.split("<!-- LANGUAGE_DIRECTIVE_END -->")[1]
                                    new_sys_content = f"{pref}<!-- LANGUAGE_DIRECTIVE_START -->\n{directive}\n<!-- LANGUAGE_DIRECTIVE_END -->{suff}"
                                    m.content = [new_sys_content] if isinstance(m.content, list) else new_sys_content
        except Exception as align_err:
            logger.error(f"[LANG] Error aligning language in llm_node: {align_err}")

        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk


def make_multilingual_agent(instructions, tools, *, language_mgr, call_state, tts_engine, voice_id):
    """Build the multilingual agent bound to the call's language/TTS state."""
    return MantraMultilingualAgent(
        instructions=instructions,
        tools=tools,
        language_mgr=language_mgr,
        call_state=call_state,
        tts_engine=tts_engine,
        voice_id=voice_id,
    )