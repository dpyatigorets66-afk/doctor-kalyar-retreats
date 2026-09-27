const {chromium}=require('playwright');
const {spawn}=require('child_process');
const cfgs=require('./configs.js');
const FF='/usr/local/lib/python3.11/dist-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2';
const [name,mode,arg]=process.argv.slice(2); // mode: still <t> | video
(async()=>{
  const b=await chromium.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome'});
  const p=await b.newPage({viewport:{width:1080,height:1920}});
  await p.goto('file://'+__dirname+'/reel.html'); await p.evaluate(()=>document.fonts.ready);
  const cfg=cfgs[name]; await p.evaluate(c=>build(c),cfg);
  if(mode==='still'){
    for(const t of arg.split(',')){await p.evaluate(t=>renderFrame(t),+t); await p.screenshot({path:`still_${name}_${t}.jpg`,type:'jpeg',quality:80});}
  } else {
    const fps=30, total=cfg.audioEnd+3.0, N=Math.round(total*fps);
    const ff=spawn(FF,['-y','-loglevel','error','-f','image2pipe','-framerate',''+fps,'-i','-','-i',cfg.audio,
      '-filter_complex','[1:a]apad,atrim=0:'+total.toFixed(2)+',afade=t=out:st='+(total-0.8).toFixed(2)+':d=0.8[a]','-map','0:v','-map','[a]',
      '-c:v','libx264','-pix_fmt','yuv420p','-crf','20','-preset','medium','-c:a','aac','-b:a','160k','-movflags','+faststart','-shortest',`out_${name}.mp4`],{stdio:['pipe','inherit','inherit']});
    for(let i=0;i<N;i++){await p.evaluate(t=>renderFrame(t),i/fps); const buf=await p.screenshot({type:'jpeg',quality:92});
      if(!ff.stdin.write(buf)) await new Promise(r=>ff.stdin.once('drain',r));}
    ff.stdin.end(); await new Promise(r=>ff.on('close',r));
  }
  await b.close();
})();
