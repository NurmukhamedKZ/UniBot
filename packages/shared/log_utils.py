import asyncio
import functools
import inspect
import logging
import time

SENSITIVE_PARAMS = {"password", "unix_password", "token", "api_key", "email", "unix_email", "dsn"}


def _sanitize_args(func, args, kwargs):
    """Replace sensitive positional/keyword args with '***'."""
    param_names = list(inspect.signature(func).parameters.keys())
    safe_args = []
    for i, val in enumerate(args):
        name = param_names[i] if i < len(param_names) else f"arg{i}"
        if name == "self":
            continue
        safe_args.append("***" if name in SENSITIVE_PARAMS else repr(val)[:100])
    safe_kwargs = {
        k: "***" if k in SENSITIVE_PARAMS else repr(v)[:100]
        for k, v in kwargs.items()
    }
    return safe_args, safe_kwargs


def log_duration(func):
    """Decorator that logs function entry, exit with duration, and exceptions."""
    func_logger = logging.getLogger(func.__module__)

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        safe_args, safe_kwargs = _sanitize_args(func, args, kwargs)
        func_logger.info("%s() called | args=%s kwargs=%s", func.__name__, safe_args, safe_kwargs)
        start = time.monotonic()
        try:
            return await func(*args, **kwargs)
        except Exception:
            func_logger.exception("%s() raised after %.2fs", func.__name__, time.monotonic() - start)
            raise
        finally:
            func_logger.info("%s() finished in %.2fs", func.__name__, time.monotonic() - start)

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        safe_args, safe_kwargs = _sanitize_args(func, args, kwargs)
        func_logger.info("%s() called | args=%s kwargs=%s", func.__name__, safe_args, safe_kwargs)
        start = time.monotonic()
        try:
            return func(*args, **kwargs)
        except Exception:
            func_logger.exception("%s() raised after %.2fs", func.__name__, time.monotonic() - start)
            raise
        finally:
            func_logger.info("%s() finished in %.2fs", func.__name__, time.monotonic() - start)

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper
