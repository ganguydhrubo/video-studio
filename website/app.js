'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
let paused=reduced.matches,position=0,last=0,frame=0,selected=-1,audio=null,sound=false;
const canvas=$('#film'),ctx=canvas.getContext('2d');
const scenes=[{name:'01 / HOWRAH GHATS',title:'Go somewhere<br><em>unexpected.</em>'},{name:'02 / TEA HILLS',title:'Find your<br><em>own rhythm.</em>'},{name:'03 / HOME DESK',title:'Make the<br><em>moment last.</em>'}];
const imgs=['scene-1.jpg','scene-2.jpg','scene-3.jpg'].map(src=>{const im=new Image();im.onload=()=>draw();im.src=src;return im});
const vids=['clip-1.mp4','clip-2.mp4','clip-3.mp4'].map(src=>{const v=document.createElement('video');v.src=src;v.muted=true;v.loop=true;v.playsInline=true;v.preload='auto';v.addEventListener('loadeddata',()=>{if(!paused)v.play().catch(()=>{});draw()});return v});
function polygon(c,points,fill){c.fillStyle=fill;c.beginPath();points.forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.closePath();c.fill()}
function landscape(c,w,h,scene,t){
 const v=vids[scene];
 if(v&&v.readyState>=2&&v.videoWidth){
  const iw=v.videoWidth,ih=v.videoHeight,scale=Math.max(w/iw,h/ih),dw=iw*scale,dh=ih*scale;
  c.save();c.drawImage(v,(w-dw)/2,(h-dh)/2,dw,dh);c.restore();return;
 }
 const im=imgs[scene];
 if(im&&im.complete&&im.naturalWidth){
  const p=(t%8)/8,zoom=1.06+p*.08,iw=im.naturalWidth,ih=im.naturalHeight,scale=Math.max(w/iw,h/ih)*zoom;
  const dw=iw*scale,dh=ih*scale,fy=[.3,.32,.34][scene],dx=(w-dw)/2+Math.sin(t*.25)*12,dy=(h-dh)*fy;
  c.save();c.drawImage(im,dx,dy,dw,dh);
  const v=c.createRadialGradient(w/2,h*.42,w*.12,w/2,h/2,w*.66);v.addColorStop(0,'#102a2010');v.addColorStop(1,'#102a2078');c.fillStyle=v;c.fillRect(0,0,w,h);c.restore();return;
 }
 c.save();c.scale(w/1000,h/600);
 const sky=c.createLinearGradient(0,0,0,600); const colors=[['#8daea8','#dce0ba'],['#99c1bc','#dbe2c6'],['#c6b599','#eee0ba']][scene];sky.addColorStop(0,colors[0]);sky.addColorStop(1,colors[1]);c.fillStyle=sky;c.fillRect(0,0,1000,600);
 const light=c.createRadialGradient(750,120,0,750,120,260);light.addColorStop(0,'#fff3cb80');light.addColorStop(1,'#fff3cb00');c.fillStyle=light;c.fillRect(0,0,1000,600);c.fillStyle='#f5edcb';c.beginPath();c.arc(750,120,scene===2?45:22,0,Math.PI*2);c.fill();
 const drift=Math.sin(t*.1)*12;
 if(scene===0){
 polygon(c,[[0,360],[85,285],[170,305],[310,100],[425,295],[550,155],[680,315],[790,210],[1000,340],[1000,600],[0,600]],'#849e91');
 polygon(c,[[220,237],[310,100],[375,213],[321,182],[298,197],[278,172]],'#d6ded0');
 polygon(c,[[460,263],[550,155],[610,253],[564,219],[540,228],[526,207]],'#cbd6c7');
 polygon(c,[[0,330],[125,369],[250,313],[460,420],[640,330],[760,354],[1000,282],[1000,600],[0,600]],'#4f7967');
 const lake=c.createLinearGradient(0,400,0,600);lake.addColorStop(0,'#94b9a6');lake.addColorStop(1,'#315f54');polygon(c,[[300,430],[650,415],[890,600],[0,600]],lake);
 polygon(c,[[0,390],[120,418],[290,450],[130,490],[80,530],[0,550]],'#2b5544');polygon(c,[[1000,378],[870,404],[707,446],[790,493],[835,540],[1000,585]],'#315b42');
 for(let i=0;i<24;i++){const y=443+i*6;c.fillStyle=i%2?'#b8d1b31a':'#dae5c82c';c.fillRect(300-i*5+Math.sin(t*.3+i)*12,y,100+i*12,1)}
 for(let i=0;i<18;i++){const x=i*30-30+drift,y=470+i%3*24;polygon(c,[[x,y-65],[x-15,y],[x+15,y]],'#254b3c');c.fillRect(x-1,y,2,14)}
 }else if(scene===1){
 c.fillStyle='#608e8b';c.fillRect(0,280,1000,320);polygon(c,[[0,250],[170,210],[300,275],[430,293],[0,322]],'#608475');
 for(let i=0;i<34;i++){c.strokeStyle=i%3===0?'#e5eed078':'#b8d9c735';c.lineWidth=i%3===0?2:1;c.beginPath();for(let x=0;x<=1000;x+=15){const y=300+i*9+Math.sin(x*.012+t*.5+i)*3;c.lineTo(x,y)}c.stroke()}
 polygon(c,[[1000,323],[850,350],[760,410],[640,455],[610,520],[450,600],[1000,600]],'#e5d8ae');polygon(c,[[1000,310],[900,332],[825,376],[795,440],[900,478],[1000,480]],'#486c50');
 c.strokeStyle='#f2efcebb';c.lineWidth=4;c.beginPath();c.moveTo(1000,340);c.bezierCurveTo(730,360,775,480,610,540);c.lineTo(500,600);c.stroke();
 }else{
 for(let layer=0;layer<5;layer++){c.beginPath();c.moveTo(0,600);for(let x=0;x<=1010;x+=10){let y=300+layer*53+Math.sin(x*.004+layer*1.7)*70+Math.sin(x*.001+layer)*22;c.lineTo(x+drift*(layer/4),y)}c.lineTo(1010,600);c.fillStyle=['#b6a484','#c9ae7e','#aa8c62','#c7a575','#917852'][layer];c.fill()}
 }
 const vignette=c.createRadialGradient(500,240,100,500,300,640);vignette.addColorStop(0,'#102a2000');vignette.addColorStop(1,'#102a205c');c.fillStyle=vignette;c.fillRect(0,0,1000,600);c.restore();
}
function resize(){const box=canvas.getBoundingClientRect();const scale=Math.min(devicePixelRatio||1,1.5);canvas.width=Math.round(box.width*scale);canvas.height=Math.round(box.height*scale);draw()}
function draw(){let scene=Math.min(2,Math.floor(position/8));const av=vids[scene];if(av&&av.readyState>=2){const target=position%8;if(Math.abs(av.currentTime-target)>.35)try{av.currentTime=target}catch{}}landscape(ctx,canvas.width,canvas.height,scene,position);if(scene!==selected){selected=scene;vids.forEach((v,i)=>{if(i===scene&&!paused)v.play().catch(()=>{});else v.pause()});$('#scene-name').textContent=scenes[scene].name;$('#film-word').innerHTML=scenes[scene].title;$$('.clip').forEach((e,i)=>e.classList.toggle('active',i===scene))}$('#scrub').value=position;$('#playhead').style.left=`${position/24*100}%`;$('#timecode').textContent=`00:${String(Math.floor(position)).padStart(2,'0')} / 00:24`}
function state(){document.body.classList.toggle('paused',paused);$$('video').forEach(v=>paused?v.pause():v.play().catch(()=>{}));vids.forEach((v,i)=>{if(i===selected&&!paused)v.play().catch(()=>{});else v.pause()});$('#motion').textContent=paused?'▶ Resume motion':'Ⅱ Pause motion';$('#motion').setAttribute('aria-pressed',String(paused));$('#play').textContent=paused?'▶':'Ⅱ';$('#play').setAttribute('aria-label',paused?'Play preview':'Pause preview');if(audio)audio.master.gain.setTargetAtTime(sound&&!paused&&!document.hidden?.045:0,audio.context.currentTime,.3)}
function toggle(){paused=!paused;state();last=0;draw()}
$('#motion').addEventListener('click',toggle);$('#play').addEventListener('click',toggle);
$('#scrub').addEventListener('input',e=>{position=Number(e.target.value);draw()});
$$('[data-scene]').forEach(b=>b.addEventListener('click',()=>{position=Number(b.dataset.scene)*8;draw()}));
$$('[data-look]').forEach(b=>b.addEventListener('click',()=>{$('#preview').classList.remove('golden','noir');if(b.dataset.look!=='natural')$('#preview').classList.add(b.dataset.look);$$('[data-look]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b))})}));
$$('[data-format]').forEach(b=>b.addEventListener('click',()=>{$('#preview').classList.toggle('portrait',b.dataset.format==='portrait');$('#frame-format').textContent=b.dataset.format==='portrait'?'9:16 · PORTRAIT CROP':'16:9 · CINEMATIC';$$('[data-format]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b))})}));
$('#sound').addEventListener('click',async()=>{try{if(!audio){const context=new(window.AudioContext||window.webkitAudioContext)(),master=context.createGain();master.gain.value=0;master.connect(context.destination);[130.81,196,261.63,329.63].forEach((f,i)=>{const o=context.createOscillator(),g=context.createGain();o.type='sine';o.frequency.value=f;g.gain.value=.2;const l=context.createOscillator(),lg=context.createGain();l.frequency.value=.07+i*.02;lg.gain.value=.07;l.connect(lg);lg.connect(g.gain);o.connect(g);g.connect(master);o.start();l.start()});audio={context,master}}await audio.context.resume();sound=!sound;$('#sound').textContent=sound?'♫ Sound on':'♫ Sound off';$('#sound').setAttribute('aria-pressed',String(sound));state()}catch{$('#sound').textContent='Audio unavailable';$('#sound').disabled=true}});
for(let i=0;i<180;i++){const bar=document.createElement('i');bar.style.height=`${4+Math.abs(Math.sin(i*1.4)*Math.cos(i*.3))*17}px`;$('#waveform').append(bar)}
let visible=true;new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;last=0},{rootMargin:'80px'}).observe($('#studio'));
function tick(now){requestAnimationFrame(tick);if(document.hidden||!visible||paused){last=0;return}if(now-frame<1000/24)return;frame=now;if(last)position=(position+Math.min((now-last)/1000,.1))%24;last=now;draw()}
document.addEventListener('visibilitychange',()=>{last=0;state()});reduced.addEventListener('change',e=>{if(e.matches){paused=true;state()}});window.addEventListener('resize',resize);resize();state();requestAnimationFrame(tick);
