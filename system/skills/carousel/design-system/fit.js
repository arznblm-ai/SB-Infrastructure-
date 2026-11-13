// подгон дисплейных строк под ширину контейнера: [data-fit="maxpx"], строки = .l
document.fonts.ready.then(()=>{document.querySelectorAll('[data-fit]').forEach(el=>{
  const lines=[...el.querySelectorAll('.l')]; if(!lines.length) return;
  el.style.whiteSpace='nowrap';
  const w=Math.max(...lines.map(l=>l.getBoundingClientRect().width));
  const fs=parseFloat(getComputedStyle(el).fontSize);
  const max=parseFloat(el.dataset.fit)||999;
  el.style.fontSize=Math.min(max,fs*el.clientWidth/w*0.99)+'px';
});});
