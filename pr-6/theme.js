// Runs before the page is drawn, so that it never flashes the other theme. The reader's choice
// wins over the system's.
function setTheme(t){document.documentElement.dataset.bsTheme=t}
setTheme(localStorage.getItem('theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',e=>{if(!localStorage.getItem('theme'))setTheme(e.matches?'dark':'light')});
document.addEventListener('click',e=>{if(!e.target.closest('#theme'))return;
const t=document.documentElement.dataset.bsTheme=='dark'?'light':'dark';localStorage.setItem('theme',t);setTheme(t)});
