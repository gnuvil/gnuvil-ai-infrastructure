import json
import uuid
import asyncio
import aiohttp

from app.core.config import COMFYUI_BASE_URL


COMFYUI_OFF_MESSAGE = "ComfyUI가 꺼져 있습니다. 포털에서 ComfyUI를 켠 뒤 다시 시도하세요."
COMFYUI_STOPPING_MESSAGE = "ComfyUI가 종료(전환) 중이라 새 작업을 받지 않습니다. 다시 켜진 뒤 시도하세요."


class ComfyUIUnavailableError(Exception):
    pass


class ComfyUIClient:
    def __init__(self, base_url: str = COMFYUI_BASE_URL):
        self.base_url = base_url

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/system_stats", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def get_system_stats(self) -> dict | None:
        """ComfyUI's /system_stats (device name and VRAM), or None when ComfyUI is not running."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/system_stats", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    return await resp.json() if resp.status == 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    async def get_object_info(self) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/object_info") as resp:
                if resp.status != 200:
                    raise ComfyUIUnavailableError(f"ComfyUI /object_info returned {resp.status}")
                return await resp.json()

    async def get_checkpoints(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        except (KeyError, IndexError):
            return []

    async def get_loras(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["LoraLoader"]["input"]["required"]["lora_name"][0]
        except (KeyError, IndexError):
            return []

    async def get_vaes(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["VAELoader"]["input"]["required"]["vae_name"][0]
        except (KeyError, IndexError):
            return []

    async def get_unets(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["UNETLoader"]["input"]["required"]["unet_name"][0]
        except (KeyError, IndexError):
            return []

    async def get_interpolation_models(self) -> list[str]:
        info = await self.get_object_info()
        try:
            spec = info["FrameInterpolationModelLoader"]["input"]["required"]["model_name"]
            return list(spec[1].get("options", []))
        except (KeyError, IndexError, AttributeError):
            return []

    async def get_gguf_unets(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["UnetLoaderGGUF"]["input"]["required"]["unet_name"][0]
        except (KeyError, IndexError):
            return []

    async def get_text_encoders(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["CLIPLoader"]["input"]["required"]["clip_name"][0]
        except (KeyError, IndexError):
            return []

    async def get_controlnets(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["ControlNetLoader"]["input"]["required"]["control_net_name"][0]
        except (KeyError, IndexError):
            return []

    async def get_ipadapters(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["IPAdapterModelLoader"]["input"]["required"]["ipadapter_file"][0]
        except (KeyError, IndexError):
            return []

    async def get_clip_visions(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["CLIPVisionLoader"]["input"]["required"]["clip_name"][0]
        except (KeyError, IndexError):
            return []

    async def get_bbox_detectors(self) -> list[str]:
        info = await self.get_object_info()
        try:
            return info["UltralyticsDetectorProvider"]["input"]["required"]["model_name"][0]
        except (KeyError, IndexError):
            return []

    async def queue_prompt(self, workflow: dict, client_id: str) -> str:
        payload = {"prompt": workflow, "client_id": client_id}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(f"{self.base_url}/prompt", json=payload) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status != 200:
                        error = body.get("error", body) if isinstance(body, dict) else body
                        if isinstance(error, dict) and error.get("type") == "service_stopping":
                            # gnuvil_lifecycle blocks new prompts while the server is stopping ComfyUI
                            raise ComfyUIUnavailableError(COMFYUI_STOPPING_MESSAGE)
                        raise ComfyUIUnavailableError(error)
                    return body["prompt_id"]
            except aiohttp.ClientConnectionError as e:
                raise ComfyUIUnavailableError(COMFYUI_OFF_MESSAGE) from e

    async def get_history(self, prompt_id: str) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/history/{prompt_id}") as resp:
                return await resp.json()

    async def get_image_bytes(self, filename: str, subfolder: str, folder_type: str) -> bytes:
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/view", params=params) as resp:
                return await resp.read()

    async def upload_image(self, filename: str, content: bytes, overwrite: bool = True) -> dict:
        form = aiohttp.FormData()
        form.add_field("image", content, filename=filename, content_type="application/octet-stream")
        form.add_field("overwrite", "true" if overwrite else "false")
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/upload/image", data=form) as resp:
                if resp.status != 200:
                    raise ComfyUIUnavailableError(f"ComfyUI 이미지 업로드 실패 ({resp.status})")
                return await resp.json()

    async def free_memory(self) -> bool:
        payload = {"unload_models": True, "free_memory": True}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/free", json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def interrupt(self):
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/interrupt") as resp:
                return resp.status == 200

    @staticmethod
    def new_client_id() -> str:
        return str(uuid.uuid4())


comfyui_client = ComfyUIClient()
