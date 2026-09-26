/* =============================================================
   input.js — Logic เฉพาะหน้า input.html
   (ฟอร์มกรอกข้อความ + แดชบอร์ดสถิติ)
   ต้องโหลด app.js (API_BASE) และ chart.js ก่อนไฟล์นี้
   ============================================================= */

/* ── Submit form ──────────────────────────────────────────── */
// ตรวจสอบว่ามีข้อความกรอกไหม ถ้ามีให้เก็บลง sessionStorage แล้วพาไปหน้า result.html
async function submitForm() {
  const input = document.getElementById("inp-profile-text");
  const text = input?.value.trim() || "";

  if (!text) {
    input?.classList.add("is-invalid");
    document.getElementById("err-profile-text")?.classList.add("show");
    return;
  }

  sessionStorage.setItem("userProfileText", text);
  sessionStorage.removeItem("analysis");
  sessionStorage.setItem("analysisPending", "true");
  window.location.href = "result.html";
}

/* ── Filled state ── */
// ใส่/ถอด class 'filled' และซ่อน error message เมื่อผู้ใช้เริ่มพิมพ์ข้อมูล
function markFilled(el) {
  if (el.value && el.value !== '') {
    el.classList.add('filled');
    el.classList.remove('is-invalid');
    const errId = 'err-' + el.id.replace('inp-', '');
    const errEl = document.getElementById(errId);
    if (errEl) errEl.classList.remove('show');
  } else {
    el.classList.remove('filled');
  }
}

/* ── Clear ── */
// ล้างค่าฟอร์มและ session ที่เกี่ยวข้อง พร้อมซ่อนคำแนะนำ AI ที่ค้างอยู่
function clearForm() {
  const input = document.getElementById('inp-profile-text');
  if (input) input.value = '';
  input?.classList.remove('filled', 'is-invalid');
  document.querySelectorAll('.error-msg').forEach(e => e.classList.remove('show'));
  sessionStorage.removeItem('userProfile');
  sessionStorage.removeItem('userProfileText');
  clearTimeout(suggestionDebounceTimer);
  hideInputSuggestion();
}

/* ── Count-up animation ── */
// อนิเมชันนับตัวเลขไต่ขึ้นจาก 0 ไปยังค่าเป้าหมาย ใช้กับกล่องสถิติในแดชบอร์ด
function countUp(el, target, decimal = false) {
  const dur   = 800;
  const start = performance.now();
  const step  = (now) => {
    const p = Math.min((now - start) / dur, 1);
    const e = 1 - Math.pow(1 - p, 3);
    el.textContent = decimal
      ? (e * target).toFixed(1)
      : Math.floor(e * target).toLocaleString("th-TH");
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ── AI Input Assistant ──────────────────────────────────────
   ทำงานระหว่างที่ user พิมพ์ (input-time), เป็นอิสระจาก /analyze-rights
   โดยสิ้นเชิง — ไม่ต้องรอ, ไม่บล็อกปุ่ม "ตรวจสอบสิทธิ" — แค่แนะนำเฉยๆ
   ยิง request ไป /suggest-input หลัง user หยุดพิมพ์สักครู่ (debounce)
   เพื่อไม่ให้ยิงทุกตัวอักษรที่พิมพ์ ── */
// ตัวแปรเก็บ timer ของ debounce สำหรับ AI Input Assistant
let suggestionDebounceTimer = null;
const SUGGESTION_DEBOUNCE_MS = 1200;

// หน่วงเวลา (debounce) ก่อนยิงขอคำแนะนำ เพื่อไม่ให้ยิง API ทุกตัวอักษรที่พิมพ์
function scheduleInputSuggestion(el) {
  clearTimeout(suggestionDebounceTimer);
  const text = el.value.trim();
  if (!text) {
    hideInputSuggestion();
    return;
  }
  suggestionDebounceTimer = setTimeout(() => fetchInputSuggestion(text), SUGGESTION_DEBOUNCE_MS);
}

// เรียก API /suggest-input เพื่อขอคำแนะนำเติมข้อมูล ถ้า error ให้เงียบไว้เฉยๆ
// (เป็นแค่ตัวช่วยเสริม ไม่ควรกระทบการกรอกฟอร์มหลัก)
async function fetchInputSuggestion(text) {
  try {
    const res = await fetch(`${API_BASE}/suggest-input`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error("suggest-input failed");
    const data = await res.json();
    if (data.status === "can_add" && data.suggestion) {
      showInputSuggestion(data.suggestion);
    } else {
      hideInputSuggestion();
    }
  } catch (e) {
    // เงียบไว้ — เป็นแค่ตัวช่วยเสริม ไม่ควรกระทบ flow หลักของฟอร์ม
    hideInputSuggestion();
    console.warn("Input suggestion unavailable:", e);
  }
}

// แสดงกล่องคำแนะนำ AI Input Assistant พร้อมข้อความที่ backend แนะนำ
function showInputSuggestion(text) {
  const box = document.getElementById("input-suggestion");
  if (!box) return;
  box.textContent = text;
  box.classList.add("show");
}

// ซ่อนกล่องคำแนะนำและล้างข้อความ
function hideInputSuggestion() {
  const box = document.getElementById("input-suggestion");
  if (!box) return;
  box.classList.remove("show");
  box.textContent = "";
}

/* ── ชื่อย่อสิทธิ ── */
// ชื่อย่อของสิทธิแต่ละอย่าง ใช้แสดงบนแกนกราฟแท่งไม่ให้ยาวเกินไป
const SHORT_NAME = {
  "เบี้ยยังชีพผู้สูงอายุ":                   "เบี้ยผู้สูงอายุ",
  "เบี้ยความพิการ":                            "เบี้ยผู้พิการ",
  "สวัสดิการแห่งรัฐ (บัตรคนจน)":              "บัตรคนจน",
  "ประกันสังคม มาตรา 33":                      "ม.33",
  "ประกันสังคม มาตรา 39":                      "ม.39",
  "ประกันสังคม มาตรา 40":                      "ม.40",
  "เงินอุดหนุนเด็กแรกเกิด":                   "เด็กแรกเกิด",
  "สิทธิหลักประกันสุขภาพถ้วนหน้า (บัตรทอง)": "บัตรทอง",
};

/* ── ชื่อหมวดหมู่สิทธิ (Category_benefit) แบบเข้าใจง่าย ── */
// ชื่อหมวดหมู่สิทธิ (key จาก backend) แปลงเป็นชื่อภาษาไทยอ่านง่าย ใช้ในกราฟวงกลม
const CATEGORY_NAME = {
  "elderly":        "ผู้สูงอายุ",
  "disability":     "ผู้พิการ",
  "income_support": "สวัสดิการรายได้",
  "social_security": "ประกันสังคม",
  "family":         "ครอบครัว/เด็กแรกเกิด",
  "healthcare":     "สุขภาพ",
};

/* ── Load dashboard ── */
// ดึงสถิติจาก /stats แล้ววาดกราฟแท่ง (สิทธิยอดนิยม) และกราฟวงกลม (หมวดหมู่)
// ถ้า backend ไม่ตอบจะซ่อนส่วนแดชบอร์ดทั้งหมด
async function loadDashboard() {
  try {
    const res = await fetch(`${API_BASE}/stats`);
    if (!res.ok) throw new Error("stats failed");
    const data = await res.json();

    // stat boxes
    countUp(document.getElementById("stat-total"), data.total_inquiries     || 0);
    countUp(document.getElementById("stat-ai"),    data.avg_ai_response_sec || 0, true);

    // bar chart
    const top = data.top_benefits || [];
    document.getElementById("chart-skeleton").style.display = "none";

    if (top.length === 0) {
      document.getElementById("benefitChart").style.display = "none";
    } else {

    const canvas = document.getElementById("benefitChart");
    canvas.style.display = "block";

    new Chart(canvas, {
      type: "bar",
      data: {
        labels:   top.map(t => SHORT_NAME[t.benefit] || t.benefit),
        datasets: [{
          label:           "จำนวนครั้ง",
          data:            top.map(t => t.count),
          backgroundColor: [
            "rgba(27,58,107,0.80)",
            "rgba(35,80,160,0.75)",
            "rgba(27,58,107,0.65)",
            "rgba(35,80,160,0.55)",
            "rgba(27,58,107,0.45)",
          ],
          borderColor:     "rgba(27,58,107,1)",
          borderWidth:     1,
          borderRadius:    6,
        }]
      },
      options: {
        responsive:          true,
        maintainAspectRatio: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.parsed.y} ครั้ง`
            }
          }
        },
        scales: {
          x: {
            ticks: { font: { family: "Sarabun, sans-serif", size: 11 }, color: "#1A2340" },
            grid:  { display: false },
          },
          y: {
            beginAtZero: true,
            ticks: {
              stepSize: 1,
              font: { family: "Sarabun, sans-serif", size: 11 },
              color: "#8796AB",
            },
            grid: { color: "#E8EDF5" },
          }
        }
      }
    });

    } // end if (top.length > 0)

    // pie chart — หมวดหมู่สิทธิที่ถูกตรวจสอบในส่วนหลัก
    const categories = data.category_breakdown || [];
    document.getElementById("chart-skeleton-cat").style.display = "none";

    if (categories.length === 0) {
      document.getElementById("categoryChart").style.display = "none";
    } else {
      const catCanvas = document.getElementById("categoryChart");
      catCanvas.style.display = "block";

      const PIE_COLORS = [
        "rgba(27,58,107,0.85)",
        "rgba(35,80,160,0.80)",
        "rgba(201,162,39,0.85)",
        "rgba(27,58,107,0.55)",
        "rgba(35,80,160,0.50)",
        "rgba(201,162,39,0.55)",
        "rgba(135,150,171,0.75)",
      ];

      new Chart(catCanvas, {
        type: "pie",
        data: {
          labels:   categories.map(c => CATEGORY_NAME[c.category] || c.category),
          datasets: [{
            data:            categories.map(c => c.count),
            backgroundColor: categories.map((_, i) => PIE_COLORS[i % PIE_COLORS.length]),
            borderColor:     "#fff",
            borderWidth:     2,
          }]
        },
        options: {
          responsive:          true,
          maintainAspectRatio: true,
          plugins: {
            legend: {
              position: "bottom",
              labels: { font: { family: "Sarabun, sans-serif", size: 11 }, color: "#1A2340" },
            },
            tooltip: {
              callbacks: {
                label: ctx => ` ${ctx.label}: ${ctx.parsed} ครั้ง`
              }
            }
          }
        }
      });
    }

  } catch (e) {
    // ซ่อน dashboard ถ้า backend ไม่ตอบ
    document.getElementById("dashboard-section").style.display = "none";
    console.warn("Dashboard unavailable:", e);
  }
}

/* ── Restore ค่าเดิม ── */
// ตอนโหลดหน้า: คืนค่าข้อความที่เคยกรอกไว้จาก sessionStorage (ถ้ามี) แล้วโหลดแดชบอร์ด
window.addEventListener('DOMContentLoaded', () => {
  const text = sessionStorage.getItem('userProfileText');
  const input = document.getElementById('inp-profile-text');
  if (text && input) {
    input.value = text;
    markFilled(input);
  }
  loadDashboard();
});