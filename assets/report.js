function bind(){
document.querySelectorAll('.prof').forEach(p=>{const s=p.querySelector('svg'),t=p.querySelector('.tip'),l=p.querySelector('.x');
const d=JSON.parse(s.dataset.c);
s.addEventListener('pointermove',e=>{const r=s.getBoundingClientRect(),x=(e.clientX-r.left)*d.w/r.width;
if(x<d.l||x>d.r){t.style.display='none';l.style.display='none';return}
const tau=Math.pow(2,(x-d.l)/(d.r-d.l)*d.x);
t.innerHTML='within '+tau.toFixed(2)+'× of the best<br>'+d.s.map(c=>'<i style="background:'+c[1]+'"></i>'+c[0]+': '+(100*c[2].filter(v=>v<=tau).length/d.n).toFixed(1)+'%').join('<br>');
t.style.display='block';t.style.left=Math.max(0,Math.min(e.clientX-r.left+12,r.width-200))+'px';t.style.top='8px';
l.setAttribute('x1',x);l.setAttribute('x2',x);l.style.display='block'});
s.addEventListener('pointerleave',()=>{t.style.display='none';l.style.display='none'})});
const q=document.querySelector('#filter'),only=document.querySelector('#failed');
document.querySelectorAll('table.cases').forEach(tb=>{const body=tb.tBodies[0];
const show=()=>{const v=q.value.toLowerCase();
for(const r of body.rows)r.style.display=r.cells[0].textContent.toLowerCase().includes(v)&&(!only.checked||r.querySelector('.fail'))?'':'none'};
q.addEventListener('input',show);only.addEventListener('change',show);
tb.querySelectorAll('th.s').forEach(h=>{let up=true;h.addEventListener('click',()=>{const i=h.cellIndex;up=!up;
const key=r=>{const v=r.cells[i].textContent,n=parseFloat(v);return isNaN(n)?v:n};
[...body.rows].sort((a,b)=>{const x=key(a),y=key(b),c=x<y?-1:x>y?1:0;return up?-c:c}).forEach(r=>body.append(r))})})})
}
bind();
// while a run is going, take the page again every two seconds and swap its content in place
// the browser asks the server whether the page has changed, and downloads it only if so
let seen;
if(document.querySelector('meta[name=live]'))setInterval(async()=>{
const r=await fetch(location.href,{cache:'no-cache'});if(!r.ok)return;
const v=r.headers.get('etag')||r.headers.get('last-modified');if(v&&v===seen)return;seen=v;
const d=new DOMParser().parseFromString(await r.text(),'text/html'),q=document.querySelector('#filter')?.value,c=document.querySelector('#failed')?.checked;
document.querySelector('#live').replaceWith(d.querySelector('#live'));bind();
if(q||c){const f=document.querySelector('#filter');f.value=q||'';document.querySelector('#failed').checked=c;f.dispatchEvent(new Event('input'))}},2000);
// how long ago the shown data was built, so that a live page visibly is one
setInterval(()=>document.querySelectorAll('.ago').forEach(a=>{const s=Math.max(0,Math.round((Date.now()-Date.parse(a.dataset.t))/1000));
a.textContent=s<90?s+' s ago':s<5400?Math.round(s/60)+' min ago':a.dataset.t.slice(0,16).replace('T',' ')+' UTC';
// a run that says it is running but has had no new data for 15 minutes has stalled: say so
const st=document.querySelector('.state.on');if(st)st.textContent=s>900?'stalled':'running';if(st)st.classList.toggle('stalled',s>900)}),1000);
