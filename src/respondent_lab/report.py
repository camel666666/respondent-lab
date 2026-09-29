"""Human-readable Markdown reporting of simulated results."""

from __future__ import annotations

from .models import SurveyResult


def markdown_report(result: SurveyResult) -> str:
    lines = [
        f"# {result.questionnaire.title}", "",
        "> **合成模拟报告**：所有回答均为模拟生成，不是真实问卷回收结果，",
        "> 不代表任何人群的比例，也不能用于推断现实行为。", "",
        result.questionnaire.description, "",
        f"模拟受访者：{result.summary['sample_size']} 人。",
        "样本来源：" + "；".join(f"{kind} {count} 人" for kind, count in result.metadata["panel_sources"].items()) + "。",
        "分层构成：" + "；".join(f"{segment} {count} 人" for segment, count in result.summary["segments"].items()) + "。",
        f"开放题生成：{result.metadata['answer_provider']}；复核人数：{result.metadata['reviewed_personas']}。",
        "", "## 模拟结果", "",
    ]
    for question in result.questionnaire.questions:
        item = result.summary["questions"][question.id]
        lines += [f"### {question.text}", ""]
        if question.type == "open_text":
            lines += [f"生成模拟文本 {item['response_count']} 条。", ""]
            continue
        lines += ["| 选项 | 人数 | 占模拟样本比例 |", "|---|---:|---:|"]
        for option in question.options:
            safe_option = option.replace("|", "\\|")
            lines.append(f"| {safe_option} | {item['counts'][option]} | {item['percentages'][option]:.1f}% |")
        if question.type == "multiple_choice":
            lines.append("\n多选题按模拟受访者人数计算，各选项比例之和可能超过 100%。")
        lines.append("")
    lines += [
        "## 使用边界", "",
        "这份报告适合检查问卷逻辑、演示分析流程和形成待验证的假设。",
        "如需发表关于真实人群的结论，应另行获取知情同意、设计真实抽样并收集真实回答。", "",
    ]
    return "\n".join(lines)
