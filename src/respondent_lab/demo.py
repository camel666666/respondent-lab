"""Deterministic, entirely fictional demonstration panel and questionnaire."""

from __future__ import annotations

import random

from .models import Persona, Question, Questionnaire


REGIONS = ("华北", "华东", "华南", "华中", "西南", "西北", "东北")
STUDENT_INTERESTS = ("学习工具", "游戏", "运动", "音乐", "实习", "旅行", "阅读", "短视频")
WORKER_INTERESTS = ("职业发展", "家庭", "运动", "旅行", "阅读", "理财", "音乐", "数码产品")
OCCUPATIONS = ("企业职员", "自由职业", "公共服务", "服务业", "技术岗位", "个体经营")
MAJORS = ("计算机", "经济", "医学", "法学", "教育", "工学", "文学", "艺术", "管理", "理学")
INDUSTRIES = ("互联网", "金融", "医疗", "教育", "制造", "零售服务", "公共部门")


def generate_demo_panel(seed: int = 42, per_segment: int = 500) -> list[Persona]:
    """Generate 500 students plus 500 working adults by default.

    All fields are simulated. The generator encodes arbitrary demo assumptions,
    not population shares or learned facts about real respondents.
    """
    if per_segment < 1:
        raise ValueError("per_segment must be positive")
    rng = random.Random(seed)
    panel: list[Persona] = []
    for segment in ("student", "working_adult"):
        for index in range(per_segment):
            is_student = segment == "student"
            age = rng.randint(18, 27) if is_student else rng.randint(23, 59)
            pool = STUDENT_INTERESTS if is_student else WORKER_INTERESTS
            panel.append(Persona(
                id=f"{'stu' if is_student else 'wrk'}-{index + 1:04d}",
                segment=segment,
                age=age,
                region=rng.choice(REGIONS),
                education=rng.choice(("专科", "本科", "研究生")) if is_student else rng.choice(("高中及以下", "专科", "本科", "研究生")),
                occupation="学生" if is_student else rng.choice(OCCUPATIONS),
                major=rng.choice(MAJORS) if is_student else "不适用",
                industry="不适用" if is_student else rng.choice(INDUSTRIES),
                interests=tuple(rng.sample(pool, 2)),
                traits={
                    "digital_comfort": round(rng.uniform(0.25, 1.0), 3),
                    "price_sensitivity": round(rng.uniform(0.1, 0.95), 3),
                    "time_pressure": round(rng.uniform(0.1, 0.95), 3),
                },
            ))
    return panel


def demo_questionnaire() -> Questionnaire:
    """A short example whose weights are explicit scenario assumptions."""
    return Questionnaire(
        title="校园与日常工具需求探索（合成演示）",
        description="用于测试问卷结构和报告流程；所有回答均为模拟生成。",
        questions=(
            Question(
                id="q1", text="你通常通过什么方式记录待办事项？", type="single_choice",
                options=("手机应用", "纸笔", "电脑软件", "不固定记录"),
                weights_by_segment={"student": (4, 2, 2, 1), "working_adult": (3, 2, 3, 1)},
            ),
            Question(
                id="q2", text="你最在意工具的哪些方面？（可多选）", type="multiple_choice",
                options=("易用", "价格", "隐私", "跨设备同步", "提醒功能"),
                weights_by_segment={"student": (4, 4, 2, 3, 2), "working_adult": (4, 2, 3, 4, 3)},
                min_selections=1, max_selections=3,
            ),
            Question(
                id="q3", text="你对当前使用的工具整体满意吗？", type="likert",
                options=("非常不满意", "不满意", "一般", "满意", "非常满意"),
                weights_by_segment={"student": (1, 2, 4, 4, 1), "working_adult": (1, 2, 4, 3, 1)},
            ),
            Question(id="q4", text="如果可以改进一处，你希望改进什么？", type="open_text"),
        ),
    )
