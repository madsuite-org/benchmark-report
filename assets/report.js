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
// the list of runs: a search over every column, and pages of RUNS_PER_PAGE; both kept across the
// the tab of the page shown
function markTab(){const here=location.pathname.split('/').pop()||'index.html';
document.querySelectorAll('.bar nav.tabs a').forEach(a=>{const on=a.getAttribute('href')===here;a.classList.toggle('active',on);on?a.setAttribute('aria-current','page'):a.removeAttribute('aria-current')})}
markTab();
// live refresh, which replaces the table
const RUNS_PER_PAGE=50;let runQuery='',runPage=0;
function bindRuns(){const tb=document.querySelector('#runs'),q=document.querySelector('#runsearch'),nav=document.querySelector('#runpages');
if(!tb||!q||!nav)return;q.value=runQuery;
const show=()=>{const v=runQuery.toLowerCase(),rows=[...tb.tBodies[0].rows],hit=rows.filter(r=>r.textContent.toLowerCase().includes(v)),
pages=Math.max(1,Math.ceil(hit.length/RUNS_PER_PAGE));runPage=Math.min(runPage,pages-1);
rows.forEach(r=>r.style.display='none');hit.slice(runPage*RUNS_PER_PAGE,(runPage+1)*RUNS_PER_PAGE).forEach(r=>r.style.display='');
nav.innerHTML=pages<2?'':[...Array(pages).keys()].map(i=>'<a href="#" data-p="'+i+'"'+(i===runPage?' class="active" aria-current="page"':'')+'>'+(i+1)+'</a>').join(' ');
nav.querySelectorAll('a').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();runPage=+a.dataset.p;show()}))};
q.addEventListener('input',()=>{runQuery=q.value;runPage=0;show()});show()}
bindRuns();
bind();
document.querySelectorAll('pre.log').forEach(b=>b.scrollTop=b.scrollHeight);
// while a run is going, take the page again every two seconds and swap its content in place
// the browser asks the server whether the page has changed, and downloads it only if so
let seen,touched=0,pressed=false;
// the new content waits while the reader is scrolling or touching the page: swapping it in then
// would cut the gesture short and throw the table back
for(const k of ['scroll','wheel','touchmove'])addEventListener(k,()=>touched=Date.now(),{capture:true,passive:true});
addEventListener('pointerdown',()=>{pressed=true;touched=Date.now()},{capture:true,passive:true});
for(const k of ['pointerup','pointercancel','touchend'])addEventListener(k,()=>{pressed=false;touched=Date.now()},{capture:true,passive:true});
if(document.querySelector('meta[name=live]'))setInterval(async()=>{
if(pressed||Date.now()-touched<2500)return;
// nor while the reader is filling in a form (adding a column, say), whose input it would throw away
const typing=()=>document.activeElement?.matches?.('#live input:not([type=hidden]),#live select,#live textarea');
if(typing())return;
const r=await fetch(location.href,{cache:'no-cache'});if(!r.ok)return;
const v=r.headers.get('etag')||r.headers.get('last-modified');if(v&&v===seen)return;
if(pressed||Date.now()-touched<2500||typing())return;seen=v;
const d=new DOMParser().parseFromString(await r.text(),'text/html'),q=document.querySelector('#filter')?.value,c=document.querySelector('#failed')?.checked;
// keep where the reader had scrolled every table to, and any open menu, across the swap
// a log followed at its end keeps following it; anything else stays where it was
const SCROLLS='#live .wide,#live .scroll,#live pre.log';
const kept=[...document.querySelectorAll(SCROLLS)].map(b=>[b.scrollLeft,b.scrollTop,b.scrollHeight-b.scrollTop-b.clientHeight<4]);
const open=[...document.querySelectorAll('#live details[open]')].map(x=>x.querySelector('summary')?.textContent);
const FIELDS='#live form input[name]:not([type=hidden]),#live form select[name]';
const filled=[...document.querySelectorAll(FIELDS)].map(x=>[x.form?.getAttribute('action')+'|'+x.name,x.value]);
document.querySelector('#live').replaceWith(d.querySelector('#live'));bind();bindRuns();markTab();localTimes();
document.querySelectorAll(SCROLLS).forEach((b,i)=>{if(!kept[i])return;b.scrollLeft=kept[i][0];
b.scrollTop=b.matches('pre.log')&&kept[i][2]?b.scrollHeight:kept[i][1]});
document.querySelectorAll('#live details').forEach(x=>{if(open.includes(x.querySelector('summary')?.textContent))x.open=true});
document.querySelectorAll(FIELDS).forEach(x=>{const k=x.form?.getAttribute('action')+'|'+x.name,f=filled.find(e=>e[0]===k);if(f&&f[1])x.value=f[1]});
if(q||c){const f=document.querySelector('#filter');f.value=q||'';document.querySelector('#failed').checked=c;f.dispatchEvent(new Event('input'))}},2000);
// how long ago the shown data was built, so that a live page visibly is one
setInterval(()=>document.querySelectorAll('.ago').forEach(a=>{const s=Math.max(0,Math.round((Date.now()-Date.parse(a.dataset.t))/1000));
a.textContent=s<90?s+' s ago':s<5400?Math.round(s/60)+' min ago':localTime(new Date(a.dataset.t));
// a run that says it is running but has had no new data for 15 minutes has stalled: say so
const st=document.querySelector('.state.on');if(st)st.textContent=s>900?'stalled':'running';if(st)st.classList.toggle('stalled',s>900)}),1000);

// the button that copies a report's link, which anyone given it can open
document.addEventListener('click',ev=>{const b=ev.target.closest?.('.copylink');if(!b)return;
const url=new URL(b.dataset.link,location.href).href,done=()=>{const t=b.textContent;b.textContent='Copied';setTimeout(()=>b.textContent=t,1500)};
if(navigator.clipboard?.writeText)navigator.clipboard.writeText(url).then(done,()=>prompt('Copy this link',url));else prompt('Copy this link',url)});

// times are sent in UTC and shown in the reader's own time zone (Sungho)
function localTime(d){const p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())}
function localTimes(){document.querySelectorAll('time.lt').forEach(t=>{const d=new Date(t.getAttribute('datetime'));if(!isNaN(d)){t.textContent=localTime(d);t.title=t.getAttribute('datetime')}})}
localTimes();
