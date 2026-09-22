/* =============================================================
   input.js — Logic เฉพาะหน้า input.html
   (ฟอร์มกรอกข้อความ + แดชบอร์ดสถิติ)
   ต้องโหลด app.js (API_BASE) และ chart.js ก่อนไฟล์นี้
   ============================================================= */

/* ── Submit form ──────────────────────────────────────────── */
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
function clearForm() {
  const input = document.getElementById('inp-profile-text');
  if (input) input.value = '';
  input?.classList.remove('filled', 'is-invalid');
  document.querySelectorAll('.error-msg').forEach(e => e.classList.remove('show'));
  sessionStorage.removeItem('userProfile');
  sessionStorage.removeItem('userProfileText');
}

/* ── Count-up animation ── */
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

/* ── ชื่อย่อสิทธิ ── */
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

/* ── Load dashboard ── */
async function loadDashboard() {
  try {
    const res = await fetch(`${API_BASE}/stats`);
    if (!res.ok) throw new Error("stats failed");
    const data = await res.json();

    // stat boxes
    countUp(document.getElementById("stat-total"), data.total_inquiries    || 0);
    countUp(document.getElementById("stat-avg"),   data.avg_benefits       || 0, true);
    countUp(document.getElementById("stat-ai"),    data.avg_ai_response_ms || 0);

    // bar chart
    const top = data.top_benefits || [];
    document.getElementById("chart-skeleton").style.display = "none";

    if (top.length === 0) {
      document.getElementById("benefitChart").style.display = "none";
      return;
    }

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

  } catch (e) {
    // ซ่อน dashboard ถ้า backend ไม่ตอบ
    document.getElementById("dashboard-section").style.display = "none";
    console.warn("Dashboard unavailable:", e);
  }
}

/* ── Load AI-generated insight over usage data (separate call from loadDashboard
   so a slow/unavailable LLM never blocks the plain numeric stats) ── */
async function loadInsight() {
  const el = document.getElementById("stat-insight");
  if (!el) return;
  try {
    const res = await fetch(`${API_BASE}/stats/insight`);
    if (!res.ok) throw new Error("insight failed");
    const data = await res.json();
    el.textContent = data.insight || "ยังไม่มีข้อมูลเพียงพอสำหรับสรุปในตอนนี้";
  } catch (e) {
    el.textContent = "ไม่สามารถโหลดสรุปเชิงลึกได้ในขณะนี้";
    console.warn("Insight unavailable:", e);
  }
}

/* ── Restore ค่าเดิม ── */
window.addEventListener('DOMContentLoaded', () => {
  const text = sessionStorage.getItem('userProfileText');
  const input = document.getElementById('inp-profile-text');
  if (text && input) {
    input.value = text;
    markFilled(input);
  }
  loadDashboard();
  loadInsight();
});