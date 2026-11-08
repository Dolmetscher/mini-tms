"""Машинный перевод (FR-08): провайдер подключается на сервере, ключи не покидают сервер.

Редактор не блокируется при отказе провайдера: роутер возвращает 503, клиент
продолжает работать вручную. Передача текста разрешена только если
org.mt_allowed и project.mt_enabled.
"""
import httpx

from .. import config


class MTError(Exception):
    pass


class MTProvider:
    name = "none"

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        raise NotImplementedError


class MockProvider(MTProvider):
    """Детерминированный стаб для тестов и локальной проверки сценария MT."""

    name = "mock"

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        from . import tags

        return f"[MT:{source_lang}->{target_lang}] {tags.strip_tags(text)}"


class LibreTranslateProvider(MTProvider):
    name = "libretranslate"

    def __init__(self, url: str, api_key: str = "", timeout: int = 10):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        from . import tags

        plain = tags.strip_tags(text)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    self.url,
                    json={
                        "q": plain,
                        "source": source_lang,
                        "target": target_lang,
                        "format": "text",
                        "api_key": self.api_key or None,
                    },
                )
                res.raise_for_status()
                return res.json()["translatedText"]
        except Exception as exc:  # сеть, таймаут, 4xx/5xx, неверный ответ
            raise MTError(f"Провайдер MT недоступен: {exc}") from exc


def get_provider() -> MTProvider:
    if config.MT_PROVIDER == "libretranslate":
        return LibreTranslateProvider(config.MT_URL, config.MT_API_KEY, config.MT_TIMEOUT_SECONDS)
    if config.MT_PROVIDER == "mock":
        return MockProvider()
    return MTProvider()
