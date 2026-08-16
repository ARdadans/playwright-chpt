from ..chatgpt.chat import ask as _cg_ask
from ..chatgpt.chat import ask_stream as _cg_ask_stream
from .state import shared_state


def ask_stream(page, prompt, model=None, reset=False):
    yield from _cg_ask_stream(page, prompt, model=model, reset=reset, state_dict=shared_state)


def ask(page, prompt, model=None, reset=False):
    return _cg_ask(page, prompt, model=model, reset=reset, state_dict=shared_state)
