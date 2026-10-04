const {chromium}=require('playwright');
(async()=>{
  const b=await chromium.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome'});
  const p=await b.newPage();
  await p.goto('file://'+__dirname+'/chai.html'); await p.evaluate(()=>document.fonts.ready);
  await p.pdf({path:'../chai-5-receptov.pdf',preferCSSPageSize:true,printBackground:true});
  await p.setViewportSize({width:560,height:794});
  const n=await p.$$eval('.page',e=>e.length);
  for(let i=0;i<n;i++){const el=(await p.$$('.page'))[i]; await el.screenshot({path:`/tmp/claude-0/pg${i}.png`});}
  console.log('pages',n); await b.close();
})();
