/* =============================================================
   app.js — Frontend logic
   ============================================================= */

const API_BASE = "http://localhost:8000";

/* ── Submit form ──────────────────────────────────────────── */
async function submitForm() {
  const input = document.getElementById("inp-profile-text");
  const text = input?.value.trim() || "";

  if (!text) {
    input?.classList.add("is-invalid");
    document.getElementById("err-profile-text")?.classList.add("show");
    alert("กรุณาพิมพ์ข้อมูลที่ต้องการให้ระบบวิเคราะห์");
    return;
  }

  sessionStorage.setItem("userProfileText", text);
  sessionStorage.removeItem("analysis");
  sessionStorage.setItem("analysisPending", "true");
  window.location.href = "result.html";
}

function clearForm() {
  const input = document.getElementById("inp-profile-text");
  if (input) {
    input.value = "";
    input.classList.remove("filled", "is-invalid");
  }
  document.getElementById("err-profile-text")?.classList.remove("show");
  sessionStorage.removeItem("analysis");
  sessionStorage.removeItem("userProfileText");
}

/* ── Render results ───────────────────────────────────────── */
function renderResults(benefits) {
  const container = document.getElementById("result-content");
  if (!benefits || benefits.length === 0) {
    const bar = document.querySelector(".action-bar");
    if (bar) bar.style.display = "none";
    container.innerHTML = `
      <div class="no-result-box">
        <div class="no-result-icon">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
            <circle cx="14" cy="14" r="12" stroke="#E0900A" stroke-width="1.5"/>
            <path d="M14 9v6M14 18v1" stroke="#E0900A" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </div>
        <p class="no-result-heading">ไม่พบสิทธิที่ตรงกับข้อมูลที่กรอก</p>
        <p class="no-result-sub">กรุณากรอกข้อมูลเพิ่มเติม หรือติดต่อหน่วยงานภาครัฐในพื้นที่</p>
      </div>`;
    return;
  }

  container.innerHTML = `
    <section class="rag-hero">
      <div class="rag-kicker">RAG ANALYSIS</div>
      <h2>กำลังค้นข้อมูลสิทธิจากฐานความรู้</h2>
      <p>AI กำลังอ่านข้อมูลที่คุณกรอกและหลักฐานที่ RAG ค้นคืนมา</p>
      <div class="rag-loading"><span></span><span></span><span></span></div>
    </section>`;
}

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
    ? `<section class="more-rights"><h3>สิทธิที่อาจเกี่ยวข้องเพิ่มเติม (${analysis.additional_benefits.length})</h3><p>RAG พบรายการเพิ่มเติม คุณสามารถให้ AI สรุปรายละเอียดในรูปแบบเดียวกับผลหลักได้</p><button class="icon-btn" id="more-rights-btn" onclick="loadAdditionalRights()">ดูรายการเพิ่มเติม</button></section>`
    : "";
  currentBenefits = analysis.benefits || [];
  container.innerHTML = `<section class="rag-hero"><div class="rag-kicker">AI + RAG ANALYSIS</div><h2>ผลวิเคราะห์สิทธิประโยชน์เบื้องต้น</h2><p>${escapeHtml(analysis.summary || "")}</p></section>${cards.join("") || '<div class="no-result-box"><p class="no-result-heading">ยังไม่พบสิทธิที่มีหลักฐานเพียงพอในฐานข้อมูล</p></div>'}${additional}${questions}<p class="disclaimer">${escapeHtml(analysis.coverage_warning || "")}</p>`;
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
    renderAnalysis(analysis);
  } catch (error) {
    console.error("AI analysis error:", error);
    setResultActionsDisabled(true);
    container.innerHTML = `
      <div class="no-result-box" role="alert">
        <div class="no-result-icon">!</div>
        <p class="no-result-heading">ยังไม่สามารถวิเคราะห์ด้วย AI ได้</p>
        <p class="no-result-sub">${escapeHtml(error.name === "AbortError" ? "การวิเคราะห์ใช้เวลานานเกิน 60 วินาที" : error.message || "ไม่สามารถเชื่อมต่อกับ server ได้")}<br>กรุณาลองใหม่อีกครั้ง</p>
      </div>`;
  } finally {
    window.clearTimeout(timeoutId);
    sessionStorage.removeItem("analysisPending");
  }
}

function renderRagResults(benefits, aiBenefits = [], aiUnavailable = false) {
  const container = document.getElementById("result-content");
  const aiByName = new Map(aiBenefits.map(item => [item.name, item.detail]));
  // AI is the decision-maker.  RAG candidates that it does not select are
  // intentionally not rendered as user results.
  const displayedBenefits = aiUnavailable
    ? benefits
    : benefits.filter(benefit => aiByName.has(benefit.name));
  currentBenefits = displayedBenefits;

  if (!displayedBenefits.length) {
    container.innerHTML = `<div class="no-result-box"><p class="no-result-heading">ยังไม่พบสิทธิที่ AI เห็นว่าเกี่ยวข้องชัดเจน</p><p class="no-result-sub">ลองเพิ่มข้อมูล หรือสอบถามหน่วยงานที่เกี่ยวข้องเพื่อยืนยันสิทธิ</p></div>`;
    return;
  }

  const cards = displayedBenefits.map((benefit, index) => {
    const explanation = aiByName.get(benefit.name);
    const documents = benefit.docs.map(item => `<li>${escapeHtml(item)}</li>`).join("");
    const message = explanation
      ? escapeHtml(explanation)
      : aiUnavailable
        ? "ยังไม่สามารถสร้างคำอธิบายจาก AI ได้ แต่รายการด้านล่างเป็นผลจากเงื่อนไขและฐานความรู้ RAG ที่ระบบค้นพบ"
        : "AI ไม่ได้เลือกสิทธินี้เป็นผลลัพธ์";
    return `
      <article class="rag-card">
        <div class="rag-card-top"><span>${String(index + 1).padStart(2, "0")}</span><p>สิทธิที่เกี่ยวข้อง</p></div>
        <h3>${escapeHtml(benefit.name)}</h3>
        <div class="rag-answer"><div class="rag-answer-label">คำอธิบายจาก AI + RAG</div><p>${message}</p></div>
        <p class="rag-check-note">ผลนี้เป็นคำแนะนำจาก AI และ RAG โปรดตรวจสอบกับหน่วยงานก่อนยืนยันสิทธิ</p>
        <div class="rag-footer"><span>เอกสาร: ${documents || "ตรวจสอบกับหน่วยงาน"}</span>${benefit.link ? `<a href="${escapeHtml(benefit.link)}" target="_blank" rel="noopener noreferrer">แหล่งข้อมูล</a>` : ""}</div>
      </article>`;
  }).join("");
  container.innerHTML = `
    <section class="rag-hero"><div class="rag-kicker">RAG ANALYSIS COMPLETE</div><h2>AI แนะนำสิทธิที่อาจเกี่ยวข้อง ${displayedBenefits.length} รายการ</h2><p>คำอธิบายสร้างจากข้อมูลที่กรอกและหลักฐานที่ระบบค้นคืนมา</p></section>
    <div class="rag-results">${cards}</div>`;
}

/* ── Load AI detail (รายละเอียดแต่ละสิทธิ) ─────────────── */
async function loadAIExplanation() {
  const profile = JSON.parse(sessionStorage.getItem("userProfile") || "null");
  if (!profile) return;

  try {
    const res = await fetch(`${API_BASE}/explain`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(profile),
    });
    if (!res.ok) throw new Error("explain failed");

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const lines = decoder.decode(value).split("\n");
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const text = line.slice(6).trim();
        if (text === "[DONE]" || !text) continue;
        buffer += text;
      }
    }

    if (!buffer) throw new Error("empty AI response");
    const data = JSON.parse(buffer);
    const list = data.benefits || [];
    const benefits = JSON.parse(sessionStorage.getItem("benefits") || "[]");
    renderRagResults(benefits, list);

  } catch (e) {
    console.error("AI explain error:", e);
    const benefits = JSON.parse(sessionStorage.getItem("benefits") || "[]");
    renderRagResults(benefits, [], true);
  }
}
