import asyncio
import inspect

from laliga_fantasy_mcp.models.schemas import WeekInput
from laliga_fantasy_mcp.tools import _format_validation_error, flatten
from pydantic import ValidationError


@flatten(WeekInput)
async def _echo_week(params: WeekInput) -> str:
    return f"week={params.week}"


def test_week_99_is_friendly_error() -> None:
    result = asyncio.run(_echo_week(week=99))
    assert result.startswith("Error:")
    assert "week" in result.lower()
    assert "ValidationError" not in result
    assert "pydantic" not in result.lower()


def test_flatten_exposes_le_constraint() -> None:
    field = inspect.signature(_echo_week).parameters["week"].default
    assert getattr(field, "le", None) == 42 or (hasattr(field, "metadata") and any(
        getattr(item, "le", None) == 42 for item in getattr(field, "metadata", ())
    ))


def test_validation_error_format() -> None:
    try:
        WeekInput.model_validate({"week": 99})
    except ValidationError as exc:
        text = _format_validation_error(exc)
    else:
        raise AssertionError("expected ValidationError")
    assert text.startswith("Error:")
    assert "week" in text
