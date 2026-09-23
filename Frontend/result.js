/* =============================================================
   result.js — Logic เฉพาะหน้า result.html
   (เรียก /analyze-rights, /analyze-more-rights แล้ว render ผล)
   ต้องโหลด app.js (API_BASE) ก่อนไฟล์นี้
   ============================================================= */

let currentBenefits = [];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

function setResultActionsDisabled(disabled) {
  const copyButton = document.getElementById("copy-btn");
  if (copyButton) copyButton.disabled = disabled;
}

function renderAnalysisLoading() {
  const container = document.getElementById("result-content");
  if (!container) return;
  setResultActionsDisabled(true);
  container.innerHTML = `
    <section class="rag-hero rag-hero-loading" aria-live="polite" aria-busy="true">
      <div class="rag-kicker">AI + RAG ANALYSIS</div>
      <h2>กำลังวิเคราะห์ข้อมูลของคุณ</h2>
      <p>AI กำลังอ่านข้อมูลที่คุณพิมพ์และค้นหาหลักฐานจากฐานความรู้</p>
      <div class="rag-loading" aria-label="กำลังโหลด"><span></span><span></span><span></span></div>
    </section>`;
}

function renderAnalysis(analysis) {
  const container = document.getElementById("result-content");
  if (!container) return;
  setResultActionsDisabled(false);
  const labels = {
    likely_eligible: "อาจมีสิทธิ",
    needs_verification: "อาจมีสิทธิ แต่ต้องตรวจสอบเพิ่ม",
    not_eligible: "ยังไม่น่ามีสิทธิจากข้อมูลที่ระบุ",
  };
  const grouped = ["likely_eligible", "needs_verification", "not_eligible"];
  const cards = grouped.flatMap(status => (analysis.benefits || [])
    .filter(item => item.status === status)
    .map(item => {
      const missing = (item.missing_information || []).length
        ? `<h4>ข้อมูลที่ต้องตรวจเพิ่ม</h4><ul>${item.missing_information.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`
        : "";
      const benefitInfo = (item.sources || []).map(source => {
        const docs = (source.docs || []).length
          ? `<ul>${source.docs.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`
          : "<p>ไม่ระบุ</p>";
        const contacts = (source.contact || []).length
          ? `<ul>${source.contact.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`
          : "<p>ไม่ระบุ</p>";
        const link = source.url
          ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">เปิดแหล่งข้อมูล</a>`
          : "ไม่มีลิงก์อ้างอิงในฐานข้อมูล";
        return `<section class="benefit-facts"><h4>ข้อมูลสิทธิจากฐานข้อมูล</h4><dl><dt>คำอธิบายสิทธิ</dt><dd>${escapeHtml(source.short_description || "ไม่ระบุ")}</dd><dt>ผลประโยชน์</dt><dd>${escapeHtml(source.benefit_details || "ไม่ระบุ")}</dd><dt>เอกสารที่ต้องใช้</dt><dd>${docs}</dd><dt>ติดต่อ</dt><dd>${contacts}</dd><dt>URL</dt><dd>${link}</dd></dl></section>`;
      }).join("");
      return `<article class="rag-card status-${status}"><div class="rag-card-top"><p>${labels[status]}</p></div><h3>${escapeHtml(item.name)}</h3><div class="rag-answer"><div class="rag-answer-label">สรุปสำหรับคุณ</div><p>${escapeHtml(item.explanation)}</p></div>${benefitInfo}${missing}</article>`;
    }));
  const questions = (analysis.follow_up_questions || []).length
    ? `<section class="rag-card"><h3>คำถามเพื่อให้วิเคราะห์ได้แม่นยำขึ้น</h3><ul>${analysis.follow_up_questions.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul></section>`
    : "";
  const additional = (analysis.additional_benefits || []).length
    ? `<section class="more-rights"><h3>สิทธิที่อาจเกี่ยวข้องเพิ่มเติม (${analysis.additional_benefits.length})</h3><p>พบรายการเพิ่มเติม คุณสามารถให้ AI สรุปรายละเอียดในรูปแบบเดียวกับผลหลักได้</p><button class="icon-btn" id="more-rights-btn" onclick="loadAdditionalRights()">ดูรายการเพิ่มเติม</button></section>`
    : "";

  currentBenefits = analysis.benefits || [];
  container.innerHTML = `<section class="rag-hero"><div class="rag-kicker">AI + RAG ANALYSIS</div><h2>ผลวิเคราะห์สิทธิประโยชน์เบื้องต้น</h2><p>${escapeHtml(analysis.summary || "")}</p></section>${cards.join("") || '<div class="no-result-box"><p class="no-result-heading">โปรดตรวจสอบคำถามของคุณ</p></div>'}${additional}${questions}`;

  const disclaimerEl = document.getElementById("disclaimer-text");
  if (disclaimerEl) {
    if (!disclaimerEl.dataset.baseText) {
      disclaimerEl.dataset.baseText = disclaimerEl.textContent.trim();
    }
    disclaimerEl.textContent = analysis.coverage_warning
      ? `${disclaimerEl.dataset.baseText} ${analysis.coverage_warning}`
      : disclaimerEl.dataset.baseText;
  }
}

/* ── เรียก /analyze-rights ด้วยข้อความที่กำหนด (ใช้ทั้งตอนโหลดครั้งแรกและตอนส่งข้อมูลเพิ่มเติม) ── */
async function runAnalysis(text) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 60000);
  try {
    const res = await fetch(`${API_BASE}/analyze-rights`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }
    const analysis = await res.json();
    sessionStorage.setItem("analysis", JSON.stringify(analysis));
    return analysis;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("การวิเคราะห์ใช้เวลานานเกิน 60 วินาที");
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function loadAdditionalRights() {
  const analysis = JSON.parse(sessionStorage.getItem("analysis") || "null");
  const text = sessionStorage.getItem("userProfileText") || "";
  const additional = analysis?.additional_benefits || [];
  if (!text || !additional.length) return;

  const button = document.getElementById("more-rights-btn");
  if (button) { button.disabled = true; button.textContent = "กำลังวิเคราะห์สิทธิเพิ่มเติม..."; }
  try {
    const res = await fetch(`${API_BASE}/analyze-more-rights`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, benefit_slugs: additional.map(item => item.slug) }),
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }
    const more = await res.json();
    analysis.benefits = [...(analysis.benefits || []), ...(more.benefits || [])];
    analysis.follow_up_questions = [...new Set([
      ...(analysis.follow_up_questions || []), ...(more.follow_up_questions || []),
    ])];
    analysis.additional_benefits = [];
    sessionStorage.setItem("analysis", JSON.stringify(analysis));
    renderAnalysis(analysis);
  } catch (error) {
    console.error("Additional AI analysis error:", error);
    if (button) { button.disabled = false; button.textContent = "ลองดูรายการเพิ่มเติมอีกครั้ง"; }
    alert(error.message || "ไม่สามารถวิเคราะห์สิทธิเพิ่มเติมได้");
  }
}

async function loadTextAnalysis() {
  const text = sessionStorage.getItem("userProfileText") || "";
  const container = document.getElementById("result-content");
  if (!text || !container) return;

  renderAnalysisLoading();
  try {
    const analysis = await runAnalysis(text);
    renderAnalysis(analysis);
  } catch (error) {
    console.error("AI analysis error:", error);
    setResultActionsDisabled(true);
    container.innerHTML = `
      <div class="no-result-box" role="alert">
        <div class="no-result-icon">!</div>
        <p class="no-result-heading">ยังไม่สามารถวิเคราะห์ด้วย AI ได้</p>
        <p class="no-result-sub">${escapeHtml(error.message || "ไม่สามารถเชื่อมต่อกับ server ได้")}<br>กรุณาลองใหม่อีกครั้ง</p>
      </div>`;
  } finally {
    sessionStorage.removeItem("analysisPending");
  }
}

/* ── หน้า load: ตรวจ session แล้วเลือก flow ที่เหมาะสม ── */
window.addEventListener("DOMContentLoaded", () => {
  const raw = sessionStorage.getItem("analysis");
  if (raw) {
    try {
      const analysis = JSON.parse(raw);
      currentBenefits = analysis.benefits || [];
      renderAnalysis(analysis);
      return;
    } catch (error) {
      console.warn("Stored analysis is invalid:", error);
      sessionStorage.removeItem("analysis");
    }
  }

  if (!sessionStorage.getItem("userProfileText")) {
    window.location.href = "input.html";
    return;
  }
  loadTextAnalysis();
});

/* ── คัดลอกผลลัพธ์เป็นข้อความ ── */
function copyResults() {
  if (currentBenefits.length === 0) return;
  let text = `ผลการวิเคราะห์สิทธิประโยชน์ภาครัฐเบื้องต้น\nพบรายการ ${currentBenefits.length} รายการ\n${"=".repeat(40)}\n\n`;
  currentBenefits.forEach((b, i) => {
    text += `${i + 1}. ${b.name}\n`;
    text += `   สถานะ: ${b.status}\n`;
    text += `   ${b.explanation}\n`;
    text += `   ตรวจเพิ่ม: ${(b.missing_information || []).join(", ") || "-"}\n\n`;
  });
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById("copy-btn");
    const ori = btn.innerHTML;
    btn.classList.add("copied");
    btn.innerHTML = "✓ คัดลอกแล้ว";
    setTimeout(() => { btn.classList.remove("copied"); btn.innerHTML = ori; }, 2000);
  });
}