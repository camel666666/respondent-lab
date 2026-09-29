const quota = {student: 100, working_adult: 100};
const demoCounts = {"手机应用": 96, "纸笔": 42, "电脑软件": 40, "不固定记录": 22};
let bundledDemo = null;

function selectedDemoCounts() {
  const major = document.querySelector("#major-filter").value;
  const industry = document.querySelector("#industry-filter").value;
  const selected = [
    ...bundledDemo.responses.filter(item => item.segment === "student" && (!major || item.major === major)).slice(0, quota.student),
    ...bundledDemo.responses.filter(item => item.segment === "working_adult" && (!industry || item.industry === industry)).slice(0, quota.working_adult),
  ];
  const counts = Object.fromEntries(Object.keys(demoCounts).map(option => [option, 0]));
  for (const response of selected) {
    const answer = response.answers?.q1;
    if (Object.hasOwn(counts, answer)) counts[answer] += 1;
  }
  return counts;
}

function availableCount(segment) {
  if (!bundledDemo?.responses) return 500;
  const major = document.querySelector("#major-filter").value;
  const industry = document.querySelector("#industry-filter").value;
  return bundledDemo.responses.filter(item => item.segment === segment &&
    (segment !== "student" || !major || item.major === major) &&
    (segment !== "working_adult" || !industry || item.industry === industry)).length;
}

function renderQuota() {
  const total = quota.student + quota.working_adult;
  const studentPercent = total ? Math.round(quota.student / total * 100) : 0;
  document.querySelector("#student-count").textContent = quota.student;
  document.querySelector("#worker-count").textContent = quota.working_adult;
  document.querySelector("#total-sample").textContent = total;
  document.querySelector("#student-share").textContent = `${studentPercent}%`;
  document.querySelector("#worker-share").textContent = `${100 - studentPercent}%`;
  document.querySelector("#student-bar").style.width = `${studentPercent}%`;
  document.querySelector("#worker-bar").style.width = `${100 - studentPercent}%`;
}

function renderChart(counts) {
  const entries = Object.entries(counts).filter(([, count]) => Number.isFinite(Number(count)));
  const total = entries.reduce((sum, [, count]) => sum + Number(count), 0);
  const chart = document.querySelector("#chart");
  chart.replaceChildren();
  for (const [label, count] of entries) {
    const percent = total ? Math.round(Number(count) / total * 100) : 0;
    const row = document.createElement("div");
    row.className = "bar-row";
    const name = document.createElement("span");
    name.textContent = label;
    const track = document.createElement("div");
    track.className = "bar-track";
    const bar = document.createElement("span");
    bar.className = "bar-fill";
    bar.style.width = `${percent}%`;
    track.append(bar);
    const value = document.createElement("strong");
    value.textContent = `${percent}%`;
    row.append(name, track, value);
    chart.append(row);
  }
}

function renderDemo() {
  const total = quota.student + quota.working_adult;
  if (!total) return;
  let counts;
  if (bundledDemo?.responses?.length) {
    counts = selectedDemoCounts();
  } else {
    if (document.querySelector("#major-filter").value || document.querySelector("#industry-filter").value) {
      window.alert("细分分组需要加载随仓库提供的演示结果。请通过本地服务器打开页面。");
      return;
    }
    const factor = total / 200;
    counts = Object.fromEntries(Object.entries(demoCounts).map(([key, value]) => [key, Math.round(value * factor)]));
  }
  renderChart(counts);
  document.querySelector("#metric-count").textContent = total;
  document.querySelector("#metric-questions").textContent = "04";
  document.querySelector("#chart-title").textContent = "你通常通过什么方式记录待办事项？";
  document.querySelector("#result-subtitle").textContent = bundledDemo
    ? "已从随仓库提供的虚构面板抽取样本；修改配额可重新查看分布。"
    : "本地预览：数值根据示例规则缩放。导入命令行结果可查看完整调查。";
  document.querySelector("#results").scrollIntoView({behavior: "smooth"});
}

function loadResult(data) {
  if (!data || !data.summary || !data.summary.questions || !data.questionnaire) throw new Error("这不是 Respondent Lab 结果文件");
  const match = Object.entries(data.summary.questions).find(([, question]) => question && question.counts && Object.keys(question.counts).length);
  if (!match) throw new Error("结果文件里没有可绘制的选择题");
  const [questionId, question] = match;
  const description = data.questionnaire.questions?.find(item => item.id === questionId)?.text || "回答分布";
  renderChart(question.counts);
  document.querySelector("#chart-title").textContent = description;
  document.querySelector("#metric-count").textContent = data.summary.sample_size ?? data.responses?.length ?? "—";
  document.querySelector("#metric-questions").textContent = String(data.questionnaire.questions?.length ?? 0).padStart(2, "0");
  document.querySelector("#result-subtitle").textContent = `${data.questionnaire.title} · 已导入模拟结果`;
  document.querySelector("#results").scrollIntoView({behavior: "smooth"});
}

document.querySelectorAll("[data-step]").forEach(button => button.addEventListener("click", () => {
  const [segment, amount] = button.dataset.step.split(":");
  quota[segment] = Math.min(availableCount(segment), Math.max(0, quota[segment] + Number(amount)));
  renderQuota();
}));
document.querySelectorAll("#major-filter,#industry-filter").forEach(select => select.addEventListener("change", () => {
  quota.student = Math.min(quota.student, availableCount("student"));
  quota.working_adult = Math.min(quota.working_adult, availableCount("working_adult"));
  renderQuota();
}));
document.querySelector("#simulate").addEventListener("click", renderDemo);
document.querySelector("#load-demo").addEventListener("click", renderDemo);
document.querySelector("#file-input").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try { loadResult(JSON.parse(await file.text())); }
  catch (error) { window.alert(error.message); }
  finally { event.target.value = ""; }
});
renderQuota();
renderChart(demoCounts);
fetch("demo-results.json").then(response => response.ok ? response.json() : null)
  .then(data => { bundledDemo = data; renderQuota(); if (bundledDemo?.responses?.length) renderChart(selectedDemoCounts()); })
  .catch(() => { bundledDemo = null; });
