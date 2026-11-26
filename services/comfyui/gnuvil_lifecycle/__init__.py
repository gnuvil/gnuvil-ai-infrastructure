"""Install the host lifecycle hook when ComfyUI loads the allow-listed extension."""

from server import PromptServer

from .lifecycle import install

NODE_CLASS_MAPPINGS = {}

install(PromptServer.instance)
