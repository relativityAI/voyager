import re

_CAMEL_TO_SNAKE_CACHE: dict[str, str] = {}


def camel_to_snake(name: str) -> str:
    cached = _CAMEL_TO_SNAKE_CACHE.get(name)
    if cached:
        return cached
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    result = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
    _CAMEL_TO_SNAKE_CACHE[name] = result
    return result


def build_tag_map(camel_keys: list[str]) -> dict[str, str]:
    return {k: camel_to_snake(k) for k in camel_keys}


def build_reverse_tag_map(snake_to_camel: dict[str, str]) -> dict[str, str]:
    return {v: k for k, v in snake_to_camel.items()}
