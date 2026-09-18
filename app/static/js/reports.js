(async () => {
  if (!window.Chart) return;
  const input = document.getElementById('reportYear');
  let charts = [];
  async function render() {
    charts.forEach(c => c.destroy()); charts = [];
    const data = await (await fetch('/api/reports/monthly?year='+input.value)).json();
    const muted = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
    const border = getComputedStyle(document.documentElement).getPropertyValue('--border').trim();
    const scales = {x:{ticks:{color:muted},grid:{display:false}},y:{ticks:{color:muted,callback:v=>'Rp '+Intl.NumberFormat('id-ID',{notation:'compact'}).format(v)},grid:{color:border}}};
    charts.push(new Chart(document.getElementById('yearlyChart'),{type:'bar',data:{labels:data.labels,datasets:[{label:'Income',data:data.income},{label:'Expense',data:data.expense}]},options:{responsive:true,maintainAspectRatio:false,scales}}));
    charts.push(new Chart(document.getElementById('netChart'),{type:'line',data:{labels:data.labels,datasets:[{label:'Net cash flow',data:data.net,tension:.35,fill:true}]},options:{responsive:true,maintainAspectRatio:false,scales}}));
  }
  input.addEventListener('change', render); render();
})();
