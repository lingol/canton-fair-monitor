from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class FairWindow:
    edition: Optional[int]
    season: str
    phase: int
    start_date: date
    end_date: date
    event_type: str
    source_name: str
    source_url: str

    @property
    def event_key(self) -> str:
        edition = str(self.edition) if self.edition is not None else "unknown"
        return f"{edition}:{self.season}:{self.phase}:{self.event_type}"


@dataclass(frozen=True)
class CommuteMessage:
    target_date: date
    phase: int
    edition: Optional[int]

    @property
    def event_label(self) -> str:
        edition = f"第 {self.edition} 届广交会" if self.edition else "广交会"
        phase_names = {1: "第一期", 2: "第二期", 3: "第三期"}
        return f"{edition}{phase_names[self.phase]}"

    def email_subject(self) -> str:
        return "明天广交会期间，建议不要开车上班"

    def email_text(self) -> str:
        return (
            f"明天（{self.target_date.isoformat()}）为{self.event_label}展期。\n\n"
            "琶洲、新港东路、阅江路、科韵路及周边道路可能出现明显拥堵。\n"
            "建议改乘地铁、错峰出行，或提前调整通勤路线。\n\n"
            "本消息由广交会通勤提醒系统自动发送。"
        )

    def wecom_markdown(self) -> str:
        return (
            "## 明天建议不要开车上班\n\n"
            f"明天（{self.target_date.isoformat()}）为**{self.event_label}**展期。\n\n"
            "琶洲、新港东路、阅江路、科韵路及周边道路可能出现明显拥堵。\n\n"
            "建议改乘地铁、错峰出行，或提前调整通勤路线。"
        )
