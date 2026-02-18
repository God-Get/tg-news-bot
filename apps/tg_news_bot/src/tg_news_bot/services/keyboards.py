"""Keyboard rendering for draft states."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram_publisher import ButtonSpec, keyboard_from_specs
from tg_news_bot.config import PostFormattingSettings
from tg_news_bot.db.models import Draft, DraftState
from tg_news_bot.services.workflow_types import DraftAction
from tg_news_bot.telegram.callbacks import build_callback

_DEFAULT_FORMATTING = PostFormattingSettings()


def build_state_keyboard(draft: Draft, state: DraftState):
    source_url = draft.normalized_url
    source_button = ButtonSpec(text="Источник", url=source_url)

    if state == DraftState.INBOX:
        rows = [
            [
                ButtonSpec(
                    text="В редакцию",
                    callback_data=build_callback(draft.id, DraftAction.TO_EDITING),
                )
            ],
            [
                ButtonSpec(
                    text="В архив",
                    callback_data=build_callback(draft.id, DraftAction.TO_ARCHIVE),
                )
            ],
            [source_button],
        ]
    elif state == DraftState.EDITING:
        rows = [
            [
                ButtonSpec(
                    text="Сделать выжимку",
                    callback_data=build_callback(draft.id, "process_now"),
                )
            ],
            [
                ButtonSpec(
                    text="В публикацию",
                    callback_data=build_callback(draft.id, DraftAction.TO_READY),
                )
            ],
            [
                ButtonSpec(
                    text="В архив",
                    callback_data=build_callback(draft.id, DraftAction.TO_ARCHIVE),
                )
            ],
            [source_button],
        ]
    elif state == DraftState.READY:
        rows = [
            [
                ButtonSpec(
                    text="Publish сейчас",
                    callback_data=build_callback(draft.id, DraftAction.PUBLISH_NOW),
                )
            ],
            [
                ButtonSpec(
                    text="Schedule",
                    callback_data=build_callback(draft.id, "schedule_open"),
                )
            ],
            [
                ButtonSpec(
                    text="Edit",
                    callback_data=build_callback(draft.id, DraftAction.TO_EDITING),
                )
            ],
            [
                ButtonSpec(
                    text="В архив",
                    callback_data=build_callback(draft.id, DraftAction.TO_ARCHIVE),
                )
            ],
            [source_button],
        ]
    elif state == DraftState.SCHEDULED:
        rows = [
            [
                ButtonSpec(
                    text="Изменить время",
                    callback_data=build_callback(draft.id, "schedule_open"),
                )
            ],
            [
                ButtonSpec(
                    text="Опубликовать сейчас",
                    callback_data=build_callback(draft.id, DraftAction.PUBLISH_NOW),
                )
            ],
            [
                ButtonSpec(
                    text="Отменить",
                    callback_data=build_callback(draft.id, DraftAction.CANCEL_SCHEDULE),
                )
            ],
            [
                ButtonSpec(
                    text="В архив",
                    callback_data=build_callback(draft.id, DraftAction.TO_ARCHIVE),
                )
            ],
            [source_button],
        ]
    elif state == DraftState.PUBLISHED:
        rows = [
            [
                ButtonSpec(
                    text="Repost",
                    callback_data=build_callback(draft.id, DraftAction.REPOST),
                )
            ],
            [
                ButtonSpec(
                    text="В редакцию",
                    callback_data=build_callback(draft.id, DraftAction.TO_EDITING),
                )
            ],
            [
                ButtonSpec(
                    text="В архив",
                    callback_data=build_callback(draft.id, DraftAction.TO_ARCHIVE),
                )
            ],
            [source_button],
        ]
    else:
        rows = [[source_button]]

    return keyboard_from_specs(rows)


def build_schedule_keyboard(
    draft: Draft,
    *,
    menu: str,
    now: datetime,
    timezone_name: str,
    selected_day: date | None = None,
):
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    tz_row = [
        ButtonSpec(
            text=f"TZ: {timezone_name}",
            callback_data=build_callback(draft.id, "schedule_tz_info"),
        )
    ]

    def schedule_at(dt: datetime) -> str:
        ts = int(dt.astimezone(timezone.utc).timestamp())
        return build_callback(draft.id, f"schedule_at_{ts}")

    def schedule_day(day_value: date) -> str:
        return build_callback(draft.id, f"schedule_day_{day_value:%Y%m%d}")

    def schedule_time(day_value: date, hour: int, minute: int) -> str:
        return build_callback(
            draft.id,
            f"schedule_time_{day_value:%Y%m%d}_{hour:02d}{minute:02d}",
        )

    if menu == "list":
        options: list[list[ButtonSpec]] = [tz_row]
        for offset in range(1, 13):
            target = (local_now + timedelta(hours=offset)).replace(minute=0, second=0, microsecond=0)
            label = target.strftime("%H:%M")
            options.append([ButtonSpec(text=label, callback_data=schedule_at(target))])
        options.append(
            [
                ButtonSpec(
                    text="Назад",
                    callback_data=build_callback(draft.id, "schedule_open"),
                )
            ]
        )
        return keyboard_from_specs(options)

    if menu == "days":
        day_rows: list[list[ButtonSpec]] = [tz_row]
        for offset in range(0, 7):
            day_value = (local_now + timedelta(days=offset)).date()
            if offset == 0:
                label = f"Сегодня {day_value:%d.%m}"
            elif offset == 1:
                label = f"Завтра {day_value:%d.%m}"
            else:
                label = day_value.strftime("%a %d.%m")
            day_rows.append(
                [
                    ButtonSpec(
                        text=label,
                        callback_data=schedule_day(day_value),
                    )
                ]
            )
        day_rows.append(
            [
                ButtonSpec(
                    text="Назад",
                    callback_data=build_callback(draft.id, "schedule_open"),
                )
            ]
        )
        return keyboard_from_specs(day_rows)

    if menu == "times":
        day_value = selected_day or local_now.date()
        rows: list[list[ButtonSpec]] = [tz_row]
        time_options = [
            (8, 0),
            (10, 0),
            (12, 0),
            (14, 0),
            (16, 0),
            (18, 0),
            (20, 0),
            (22, 0),
        ]
        available_buttons: list[ButtonSpec] = []
        min_allowed_local = local_now + timedelta(minutes=5)
        is_today = day_value == local_now.date()
        for hour, minute in time_options:
            candidate_local = datetime(
                day_value.year,
                day_value.month,
                day_value.day,
                hour,
                minute,
                tzinfo=tz,
            )
            if is_today and candidate_local <= min_allowed_local:
                continue
            available_buttons.append(
                ButtonSpec(
                    text=f"{hour:02d}:{minute:02d}",
                    callback_data=schedule_time(day_value, hour, minute),
                )
            )

        if not available_buttons:
            rows.append(
                [
                    ButtonSpec(
                        text="На сегодня слотов нет",
                        callback_data=build_callback(draft.id, "schedule_day_menu"),
                    )
                ]
            )

        for idx in range(0, len(available_buttons), 2):
            current = available_buttons[idx : idx + 2]
            row: list[ButtonSpec] = []
            for button in current:
                row.append(button)
            rows.append(row)
        rows.append(
            [
                ButtonSpec(
                    text="К датам",
                    callback_data=build_callback(draft.id, "schedule_day_menu"),
                )
            ]
        )
        rows.append(
            [
                ButtonSpec(
                    text="Назад",
                    callback_data=build_callback(draft.id, "schedule_open"),
                )
            ]
        )
        return keyboard_from_specs(rows)

    presets: list[list[ButtonSpec]] = [
        tz_row,
        [ButtonSpec(text="+30m", callback_data=schedule_at(local_now + timedelta(minutes=30)))],
        [ButtonSpec(text="+2h", callback_data=schedule_at(local_now + timedelta(hours=2)))],
    ]

    tomorrow_10 = (local_now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    presets.append([ButtonSpec(text="Завтра 10:00", callback_data=schedule_at(tomorrow_10))])
    presets.append([ButtonSpec(text="Выбрать вручную", callback_data=build_callback(draft.id, "schedule_list"))])
    presets.append([ButtonSpec(text="Ввести вручную", callback_data=build_callback(draft.id, "schedule_manual_open"))])
    presets.append([ButtonSpec(text="Отменить ввод", callback_data=build_callback(draft.id, "schedule_manual_cancel"))])
    presets.append(
        [
            ButtonSpec(
                text="Дата и время",
                callback_data=build_callback(draft.id, "schedule_day_menu"),
            )
        ]
    )
    presets.append([ButtonSpec(text="Назад", callback_data=build_callback(draft.id, "schedule_back"))])
    return keyboard_from_specs(presets)


def build_source_button_keyboard(
    draft: Draft,
    *,
    formatting: PostFormattingSettings | None = None,
):
    fmt = formatting or _DEFAULT_FORMATTING
    buttons: list[ButtonSpec] = []
    if fmt.source_mode in {"button", "both"}:
        buttons.append(ButtonSpec(text=fmt.source_label, url=draft.normalized_url))
    if fmt.discussion_url:
        buttons.append(ButtonSpec(text=fmt.discussion_label, url=fmt.discussion_url))
    if not buttons:
        return None
    return keyboard_from_specs([buttons])
