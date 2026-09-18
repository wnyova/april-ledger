(async () => {
  if (!window.Chart) return;
  const res = await fetch('/api/dashboard');
  const data = await res.json();
  const textColor = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
  const gridColor = getComputedStyle(document.documentElement).getPropertyValue('--border').trim();
  const common = {responsive:true, maintainAspectRatio:false, plugins:{legend:{labels:{color:textColor, usePointStyle:true}}}};
  new Chart(document.getElementById('cashflowChart'), {type:'line', data:{labels:data.cashflow.labels,datasets:[{label:'Income',data:data.cashflow.income,tension:.35,borderWidth:2,pointRadius:2},{label:'Expense',data:data.cashflow.expense,tension:.35,borderWidth:2,pointRadius:2}]}, options:{...common,scales:{x:{ticks:{color:textColor},grid:{display:false}},y:{ticks:{color:textColor,callback:v=>'Rp '+Intl.NumberFormat('id-ID',{notation:'compact'}).format(v)},grid:{color:gridColor}}}}});
  const cats = data.categories.slice(0,7);
  new Chart(document.getElementById('categoryChart'), {type:'doughnut',data:{labels:cats.map(x=>x.icon+' '+x.name),datasets:[{data:cats.map(x=>x.amount),borderWidth:0}]},options:{...common,cutout:'72%',plugins:{legend:{position:'bottom',labels:{color:textColor,usePointStyle:true,padding:16}}}}});
})();
