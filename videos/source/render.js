// Рендер рилса: node render.js <name> still <t1,t2,...>  |  node render.js <name> video
// Конфиги — в configs.js. Аудио (если есть) — путь относительно этой папки.
const {chromium}=require('playwright');
const {spawn,execSync}=require('child_process');
const fs=require('fs'), path=require('path');
const cfgs=require('./configs.js');

function findFfmpeg(){
  if(process.env.FFMPEG) return process.env.FFMPEG;
  try{return execSync('command -v ffmpeg').toString().trim()}catch{}
  try{return execSync('python3 -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"').toString().trim()}catch{}
  throw new Error('ffmpeg не найден: pip install imageio-ffmpeg или задайте FFMPEG');
}
function findChrome(){
  if(process.env.CHROME) return process.env.CHROME;
  const root='/opt/pw-browsers';
  if(fs.existsSync(root)) for(const d of fs.readdirSync(root).filter(d=>/^chromium-\d+$/.test(d)).sort().reverse()){
    const p=path.join(root,d,'chrome-linux','chrome'); if(fs.existsSync(p)) return p;
  }
  return undefined; // playwright по умолчанию
}

const [name,mode,arg]=process.argv.slice(2);
const OUT=process.env.OUT_DIR||process.cwd();
(async()=>{
  const cfg=cfgs[name]; if(!cfg) throw new Error('нет конфига '+name+'; есть: '+Object.keys(cfgs).join(', '));
  const b=await chromium.launch({executablePath:findChrome()});
  const p=await b.newPage({viewport:{width:1080,height:1920}});
  await p.goto('file://'+__dirname+'/reel.html'); await p.evaluate(()=>document.fonts.ready);
  await p.evaluate(c=>build(c),cfg);
  if(mode==='still'){
    for(const t of arg.split(',')){await p.evaluate(t=>renderFrame(t),+t); await p.screenshot({path:path.join(OUT,`still_${name}_${t}.jpg`),type:'jpeg',quality:80});}
  } else {
    const fps=30, total=cfg.audioEnd+3.0, N=Math.round(total*fps);
    const audioIn=cfg.audio?['-i',path.resolve(__dirname,cfg.audio)]:['-f','lavfi','-t',total.toFixed(2),'-i','anullsrc=r=44100:cl=mono'];
    const ff=spawn(findFfmpeg(),['-y','-loglevel','error','-f','image2pipe','-framerate',''+fps,'-i','-',...audioIn,
      '-filter_complex','[1:a]apad,atrim=0:'+total.toFixed(2)+',afade=t=out:st='+(total-0.8).toFixed(2)+':d=0.8[a]','-map','0:v','-map','[a]',
      '-c:v','libx264','-pix_fmt','yuv420p','-crf','20','-preset','medium','-c:a','aac','-b:a','160k','-movflags','+faststart','-shortest',path.join(OUT,`out_${name}.mp4`)],{stdio:['pipe','inherit','inherit']});
    for(let i=0;i<N;i++){await p.evaluate(t=>renderFrame(t),i/fps); const buf=await p.screenshot({type:'jpeg',quality:92});
      if(!ff.stdin.write(buf)) await new Promise(r=>ff.stdin.once('drain',r));}
    ff.stdin.end(); await new Promise(r=>ff.on('close',r));
  }
  await b.close();
})();
