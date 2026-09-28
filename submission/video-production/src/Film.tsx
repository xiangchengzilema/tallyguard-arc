import React from 'react';
import {AbsoluteFill, Audio, Easing, Img, interpolate, Loop, OffthreadVideo, Sequence, staticFile, useCurrentFrame} from 'remotion';

const FPS = 30;
const C = {ink:'#101817', muted:'#53605e', line:'#d5dcda', paper:'#f8f9f7', teal:'#007d79', purple:'#6f50c7'};
const base:React.CSSProperties = {fontFamily:'Manrope, Arial, sans-serif',color:C.ink,background:C.paper};
const ease = Easing.bezier(0.16,1,0.3,1);
const tween=(f:number,a:number,b:number)=>interpolate(f,[a,b],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp',easing:ease});

const Mark:React.FC<{size?:number;inverse?:boolean}>=({size=60,inverse=false})=><div style={{width:size,height:size,borderRadius:size*.15,background:inverse?'#f8f9f7':C.ink,color:inverse?C.ink:'#fff',fontWeight:900,fontSize:size*.57,display:'grid',placeItems:'center',letterSpacing:'-.07em'}}>T</div>;
const Header:React.FC<{index:string;title:string;test?:boolean}>=({index,title,test})=>{const f=useCurrentFrame(),p=tween(f,0,17);return <div style={{position:'absolute',top:36,left:74,right:74,display:'flex',alignItems:'center',justifyContent:'space-between',zIndex:5,opacity:p,transform:'translateY('+((1-p)*-18)+'px)'}}>
  <div style={{display:'flex',alignItems:'center',gap:18}}><Mark size={43}/><span style={{fontSize:21,fontWeight:800}}>TallyGuard</span><span style={{width:1,height:27,background:C.line,margin:'0 4px'}}/><span style={{fontSize:21,color:C.muted}}>{title}</span></div>
  <div style={{fontFamily:'Consolas, monospace',fontSize:15,color:test?C.purple:C.muted,letterSpacing:1.2}}>{index} / 12 · {test?'ARC TESTNET':'PRODUCT UI'}</div>
</div>};
const Footer:React.FC<{text:string;test?:boolean}>=({text,test})=><div style={{position:'absolute',bottom:27,left:76,right:76,display:'flex',alignItems:'center',justifyContent:'space-between',zIndex:5,fontSize:15,color:C.muted}}><span style={{fontWeight:650}}>{text}</span><span style={{fontFamily:'Consolas, monospace',color:test?C.purple:C.teal}}>{test?'HISTORICAL TESTNET PROOF':'EVIDENCE-BOUND · FINANCE-CONTROLLED'}</span></div>;

type ScreenProps={src?:string;clip?:string;start?:number;speed?:number;zoom?:number;zoomEnd?:number;motionFrames?:number;origin?:string;originY?:string;label?:string;compactCaption?:boolean};
const Screen:React.FC<ScreenProps>=({src,clip,start=0,speed=1,zoom=1,zoomEnd=zoom,motionFrames=360,origin='50%',originY='50%'})=>{const f=useCurrentFrame(),animatedZoom=zoom+(zoomEnd-zoom)*tween(f,0,motionFrames);return <AbsoluteFill style={{background:'#0d1514',overflow:'hidden'}}>
  {src?<Img src={staticFile('media/'+src)} style={{width:'100%',height:'100%',objectFit:'cover',transform:'scale('+animatedZoom+')',transformOrigin:origin+' '+originY}}/>:<OffthreadVideo src={staticFile('media/'+clip)} startFrom={Math.round(start*FPS)} playbackRate={speed} muted style={{width:'100%',height:'100%',objectFit:'cover',transform:'scale('+animatedZoom+')',transformOrigin:origin+' '+originY}}/>}
</AbsoluteFill>};
const OpeningOverlay:React.FC=()=>{const f=useCurrentFrame(),title=tween(f,6,40),copy=tween(f,28,64);if(f>=90)return null;return <AbsoluteFill style={{background:'#0d1514',color:'#f8f9f7',pointerEvents:'none'}}>
  <div style={{position:'absolute',top:82,left:112,display:'flex',alignItems:'center',gap:18,opacity:title,transform:'translateY('+((1-title)*20)+'px)'}}><Mark size={62} inverse/><span style={{fontSize:28,fontWeight:820,letterSpacing:-.6}}>TallyGuard</span><span style={{marginLeft:18,fontSize:16,color:'#8aaea6',letterSpacing:1.5}}>INTELLIGENT ACCOUNTS PAYABLE</span></div>
  <div style={{position:'absolute',left:112,top:300,fontFamily:'Georgia, serif',fontSize:112,lineHeight:1.07,letterSpacing:-4,opacity:title,transform:'translateY('+((1-title)*60)+'px)'}}>Invoices move faster.<br/><span style={{color:'#52ccb3'}}>Authority stays with finance.</span></div>
  <div style={{position:'absolute',left:117,top:650,fontSize:29,lineHeight:1.45,color:'#c0d0ca',opacity:copy,transform:'translateY('+((1-copy)*24)+'px)'}}>Evidence-bound review. Independent approval.<br/>Programmable USDC settlement on Arc.</div>
  <div style={{position:'absolute',left:112,right:112,bottom:105,height:2,background:'#29403b'}}><div style={{height:'100%',width:(tween(f,6,108)*100)+'%',background:'#52ccb3'}}/></div>
</AbsoluteFill>};
const ApprovalBridge:React.FC=()=>{const f=useCurrentFrame(),cover=tween(f,354,378),mark=tween(f,378,404),result=tween(f,401,427);return <AbsoluteFill style={{background:'#0d1514',color:'#f8f9f7',opacity:cover,pointerEvents:'none',display:'grid',placeItems:'center'}}>
  <div style={{textAlign:'center',transform:'translateY('+((1-mark)*24)+'px)',opacity:mark}}><div style={{margin:'0 auto 32px',width:104,height:104,borderRadius:'50%',border:'2px solid #52ccb3',color:'#52ccb3',fontSize:66,display:'grid',placeItems:'center'}}>✓</div><div style={{fontFamily:'Georgia, serif',fontSize:83,letterSpacing:-2}}>Finance decision recorded.</div><div style={{marginTop:22,fontSize:29,color:'#bdd1ca'}}>TG-E1A30ADD · 1,486.25 USDC</div><div style={{marginTop:39,opacity:result,fontSize:22,color:'#52ccb3',letterSpacing:.5}}>APPROVED FOR PAYMENT · SETTLEMENT STILL PENDING</div></div>
</AbsoluteFill>};
type FilmSegment={clip:string;start:number;speed:number;duration:number};
const UIShot:React.FC<ScreenProps&{index:string;title:string;footer:string;test?:boolean;segments?:FilmSegment[]}>=({index:_index,title,footer,test:_test,compactCaption=false,segments,...screen})=>{const f=useCurrentFrame(),a=tween(f,2,20);return <AbsoluteFill style={{background:'#0d1514'}}>
  {segments?segments.map((segment,i)=><Sequence key={i} from={segments.slice(0,i).reduce((sum,part)=>sum+part.duration,0)*FPS} durationInFrames={segment.duration*FPS}><Screen clip={segment.clip} start={segment.start} speed={segment.speed}/></Sequence>):<Screen {...screen}/>}
  <div style={{position:'absolute',left:0,right:0,bottom:0,height:compactCaption?150:202,background:'linear-gradient(0deg,rgba(10,25,24,.95),rgba(10,25,24,.67) 55%,transparent)',pointerEvents:'none'}}/>
  <div style={{position:'absolute',left:65,right:65,bottom:compactCaption?20:39,color:'#fff',opacity:a,transform:'translateY('+((1-a)*20)+'px)'}}>
    <div style={{display:'flex',alignItems:'center',gap:18}}><div style={{width:compactCaption?36:32,height:compactCaption?36:32,background:'#4bceb3',color:'#0d1d1c',fontWeight:900,fontSize:compactCaption?25:23,display:'grid',placeItems:'center'}}>T</div><span style={{fontSize:compactCaption?27:26,fontWeight:850,letterSpacing:-.5}}>TallyGuard</span><span style={{color:'#85a9a1',fontSize:compactCaption?27:24}}> / </span><span style={{fontSize:compactCaption?44:39,fontWeight:780,letterSpacing:-.8}}>{title}</span></div>
    <div style={{fontSize:compactCaption?27:25,color:'#c6d7d3',marginTop:compactCaption?7:10,marginLeft:compactCaption?54:50}}>{footer}</div>
    {screen.label&&<div style={{position:'absolute',right:0,bottom:compactCaption?2:9,padding:'9px 15px',background:'rgba(9,28,25,.9)',border:'1px solid rgba(255,255,255,.42)',color:'#fff',fontSize:compactCaption?26:25,fontWeight:750}}>{screen.label}</div>}
  </div>
</AbsoluteFill>};

const MixedOutcomesShot:React.FC=()=><AbsoluteFill style={{background:'#0d1514'}}>
  <Screen clip="features.mp4" start={9} speed={.7}/>
  <div style={{position:'absolute',left:0,right:0,bottom:0,height:82,background:'rgba(7,22,20,.94)',display:'flex',alignItems:'center',justifyContent:'space-between',padding:'0 45px',color:'#fff'}}>
    <span style={{fontWeight:800,fontSize:29}}>Different evidence. Different decisions.</span>
    <span style={{fontWeight:750,fontSize:21,color:'#b5e8dc'}}>SETTLE · APPROVE · SCHEDULE · HOLD</span>
  </div>
</AbsoluteFill>;

const PainOpen:React.FC=()=>{const f=useCurrentFrame(),a=tween(f,3,28),c=tween(f,275,315);const pains=['Scattered invoice, PO, and delivery proof','Duplicate invoices and wallet risk','Slow multi-person approvals','No clear reason or request status','Bank cutoffs and delayed settlement','AI cannot hold unchecked payment authority'];return <AbsoluteFill style={{background:'#0d1514',color:'#f8f9f7',overflow:'hidden'}}>
  <div style={{position:'absolute',inset:0,background:'radial-gradient(circle at 86% 12%, #174039 0, transparent 34%), radial-gradient(circle at 10% 95%, #14302c 0, transparent 37%)'}}/>
  <div style={{position:'absolute',top:66,left:105,display:'flex',alignItems:'center',gap:20,opacity:a}}><Mark size={51} inverse/><span style={{fontSize:23,fontWeight:850}}>TallyGuard</span><span style={{fontSize:15,color:'#8fafaa',letterSpacing:2.4,marginLeft:22}}>INTELLIGENT ACCOUNTS PAYABLE</span></div>
  <div style={{position:'absolute',top:212,left:105,right:100,opacity:a,transform:'translateY('+((1-a)*42)+'px)'}}><div style={{color:'#80b7ac',fontSize:19,fontWeight:850,letterSpacing:3}}>THE COST OF A FRAGMENTED PAYMENT</div><div style={{fontFamily:'Georgia, serif',fontSize:105,lineHeight:1.04,letterSpacing:-4,marginTop:20}}>The invoice is ready.<br/><span style={{color:'#f1b990'}}>The decision is not.</span></div></div>
  <div style={{position:'absolute',top:535,left:105,right:105,display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:15}}>{pains.map((v,i)=>{const p=tween(f,60+i*32,90+i*32);return <div key={v} style={{border:'1px solid #38534c',background:'#17302b',minHeight:90,padding:'19px 21px',fontSize:20,color:'#dceae5',opacity:p,transform:'translateY('+((1-p)*24)+'px)'}}><span style={{fontFamily:'Consolas, monospace',fontSize:15,color:'#e7a785',marginRight:15}}>0{i+1}</span>{v}</div>})}</div>
  <div style={{position:'absolute',left:105,right:105,bottom:105,display:'flex',alignItems:'center',gap:25,opacity:c,transform:'translateY('+((1-c)*24)+'px)'}}><div style={{width:43,height:43,borderRadius:'50%',display:'grid',placeItems:'center',background:'#4bceb3',color:'#102420',fontSize:28}}>→</div><div style={{fontSize:27,fontWeight:760}}>AI checks evidence. Finance sets authority. Arc settles with proof.</div></div>
  <div style={{position:'absolute',bottom:45,left:105,right:105,height:2,background:'#2b423c'}}><div style={{height:'100%',width:(tween(f,0,414)*100)+'%',background:'#4bceb3'}}/></div>
</AbsoluteFill>};

const Intro:React.FC=()=>{const f=useCurrentFrame(),m=tween(f,5,28),l=tween(f,20,48),b=tween(f,78,105);return <AbsoluteFill style={{...base,padding:'87px 125px'}}>
  <div style={{display:'flex',alignItems:'center',gap:24,opacity:m,transform:'translateY('+((1-m)*24)+'px)'}}><Mark size={64}/><span style={{fontWeight:800,fontSize:26}}>TallyGuard</span><span style={{fontSize:17,color:C.muted,marginLeft:18}}>INTELLIGENT ACCOUNTS PAYABLE</span></div>
  <div style={{position:'absolute',top:310,left:124,right:124,overflow:'hidden'}}><div style={{fontFamily:'Georgia, serif',fontSize:110,lineHeight:1.04,letterSpacing:-4,opacity:l,transform:'translateY('+((1-l)*80)+'px)'}}>Invoices in.<br/><span style={{color:C.teal}}>Controlled payments out.</span></div></div>
  <div style={{position:'absolute',bottom:175,left:130,width:1000,fontSize:27,color:C.muted,lineHeight:1.46,opacity:b}}>Give AI the speed to review every payment.<br/>Keep finance in control of the money.</div>
  <div style={{position:'absolute',bottom:72,left:130,right:130,height:2,background:C.line}}><div style={{height:'100%',width:(tween(f,30,120)*100)+'%',background:C.teal}}/></div>
</AbsoluteFill>};

const Evidence:React.FC=()=>{const f=useCurrentFrame(),names=['INVOICE','PURCHASE ORDER','DELIVERY PROOF'];return <AbsoluteFill style={base}><Header index="02" title="One request. Three sources."/><div style={{position:'absolute',top:190,left:112,fontFamily:'Georgia, serif',fontSize:76,letterSpacing:-2,lineHeight:1.1}}>Money should move only<br/>when evidence agrees.</div>
  <svg width="1600" height="520" style={{position:'absolute',left:160,top:420,overflow:'visible'}}><path d="M 250 120 C 560 120 500 250 1000 250 M 250 250 C 570 250 600 250 1000 250 M 250 380 C 560 380 500 250 1000 250" fill="none" stroke={C.teal} strokeWidth="3" strokeDasharray="1050" strokeDashoffset={1050*(1-tween(f,25,75))}/></svg>
  {names.map((n,i)=>{const p=tween(f,6+i*8,27+i*8);return <div key={n} style={{position:'absolute',left:165,top:490+i*130,width:420,height:90,background:'#fff',border:'1px solid '+C.line,boxShadow:'0 10px 30px rgba(16,24,23,.06)',display:'flex',alignItems:'center',gap:22,padding:'0 28px',opacity:p,transform:'translateX('+((1-p)*-70)+'px)'}}><div style={{width:37,height:44,border:'2px solid '+C.teal,borderRadius:3,display:'grid',placeItems:'center',fontSize:18,color:C.teal}}>✓</div><span style={{fontSize:22,fontWeight:800}}>{n}</span></div>})}
  <div style={{position:'absolute',left:1160,top:605,width:580,height:180,background:C.ink,color:'#fff',display:'grid',placeItems:'center',textAlign:'center',opacity:tween(f,72,100)}}><div style={{fontSize:21,letterSpacing:2,color:'#9de2d3'}}>TALLYGUARD</div><div style={{fontFamily:'Georgia, serif',fontSize:43}}>Traceable payment request</div></div><Footer text="Invoice + PO + delivery proof become a sealed request"/>
</AbsoluteFill>};

const ArcBridge:React.FC=()=>{const f=useCurrentFrame(),appear=tween(f,0,26),photo=tween(f,70,92);const parts=[['01','Finance-authorized','payment intent'],['02','Circle developer-','controlled wallet'],['03','Arc USDC','settlement'],['04','Reconciled','receipt']];return <AbsoluteFill style={base}><Header index="10" title="Programmable settlement" test/>
  <div style={{position:'absolute',left:110,top:200,right:110,display:'flex',alignItems:'center',gap:35,opacity:appear}}>{parts.map(([num,a,b],i)=><React.Fragment key={num}><div style={{flex:1,minWidth:0,height:236,padding:'31px 25px',background:'#fff',border:'1px solid '+C.line,borderTop:'5px solid '+(i===2?C.purple:C.teal)}}><div style={{fontFamily:'Consolas,monospace',color:C.teal,fontSize:18}}>{num}</div><div style={{fontSize:26,fontWeight:780,lineHeight:1.15,marginTop:34}}>{a}<br/>{b}</div></div>{i<3&&<div style={{fontSize:36,color:C.teal}}>→</div>}</React.Fragment>)}</div>
  <div style={{position:'absolute',left:110,top:505,right:110,height:435,background:'#fff',border:'1px solid '+C.line,overflow:'hidden',opacity:photo,transform:'translateY('+((1-photo)*28)+'px)'}}><Img src={staticFile('media/arc-activity.png')} style={{width:'100%',height:'100%',objectFit:'cover',objectPosition:'center 24%'}}/><div style={{position:'absolute',right:18,bottom:18,background:'rgba(255,255,255,.96)',border:'1px solid '+C.line,padding:'10px 15px',fontSize:16,fontWeight:850}}>Separate historical Arc Testnet activity</div></div>
  <Footer text="Synthetic workflow tests · no production customers claimed" test/>
</AbsoluteFill>};

const Outro:React.FC=()=>{const f=useCurrentFrame(),scale=interpolate(f,[0,42],[1.4,1],{extrapolateRight:'clamp',easing:ease}),a=tween(f,4,35),b=tween(f,35,83);return <AbsoluteFill style={{...base,background:'#0d1514',color:'#f8f9f7',display:'grid',placeItems:'center'}}><div style={{display:'flex',alignItems:'center',gap:32,transform:'scale('+scale+')',opacity:a}}><Mark size={108} inverse/><div style={{fontSize:67,fontWeight:850,letterSpacing:-2}}>TallyGuard</div></div><div style={{position:'absolute',top:670,left:0,right:0,textAlign:'center',fontFamily:'Georgia, serif',fontSize:45,opacity:b,transform:'translateY('+((1-b)*18)+'px)'}}>Agent speed. Finance control. Proof on Arc.</div><div style={{position:'absolute',bottom:80,fontSize:17,letterSpacing:2,color:'#4bceb3'}}>INTELLIGENT ACCOUNTS PAYABLE</div></AbsoluteFill>};

const shots:[number,number,React.ReactNode][]=[
  [0,14,<PainOpen/>],
  [14,18,<UIShot index="01" title="One controlled path from invoice to receipt" footer="Five visible steps: evidence · match · policy · settlement · audit" clip="landing-final.mp4" start={0} speed={1} compactCaption/>],
  [32,11,<UIShot index="02" title="One request · three evidence sources" footer="Invoice + purchase order + delivery proof; a person confirms the extracted fields" clip="manual-v20.mp4" start={1.7} speed={.27} compactCaption label="Real web interaction"/>],
  [43,12,<UIShot index="03" title="Review unfolds before the result" footer="TG-A894F253 · evidence sealed · controls checked · finance review required" clip="manual-v20.mp4" start={4.7} speed={.42} compactCaption label="Live submission modal"/>],
  [55,12,<UIShot index="04" title="Finance selects and reviews the same request" footer="The checkbox, evidence, supplier, wallet, and policy decision remain linked" clip="manual-v20.mp4" start={11.3} speed={.27} compactCaption label="Finance backend"/>],
  [67,11,<UIShot index="05" title="Independent approval records a reason" footer="Decision note: “Evidence and supplier wallet verified.” Finance still controls settlement." clip="manual-v20.mp4" start={15.6} speed={.26} compactCaption label="Separate authority"/>],
  [78,10,<UIShot index="06" title="Finance executes a separate settlement step" footer="Approval is not payment; finance still authorizes the final transfer" clip="manual-v20.mp4" start={19.1} speed={.22} compactCaption label="Public demo · simulation"/>],
  [88,8,<UIShot index="07" title="The requester receives a final receipt" footer="TG-A894F253 · status, finance note, progress, and receipt under one ID" clip="manual-v20.mp4" start={21.85} speed={.19} compactCaption label="Public demo · simulation"/>],
  [96,13,<UIShot index="08" title="Finance explicitly enables no-touch authority" footer="50 USDC per payment · 450 USDC per day · emergency stop · versioned policy" clip="auto-v20.mp4" start={0} speed={.26} compactCaption label="New 50/450 policy requires finance activation"/>],
  [109,11,<UIShot index="09" title="A 40 USDC request is checked" footer="A person confirms the amount; evidence, wallet, budget, and policy are evaluated" clip="auto-v20.mp4" start={4.2} speed={.43} compactCaption label="Live submit and decision"/>],
  [120,8,<UIShot index="10" title="Within the limits · automatically settled" footer="No human exception was needed; the requester opens the simulation receipt" clip="auto-v20.mp4" start={9.0} speed={.56} compactCaption label="Public demo · simulation"/>],
  [128,18,<UIShot index="11" title="A rejected request carries its reason" footer="The approver enters a reason; the requester opens it and starts a corrected request" segments={[{clip:'return-v17.mp4',start:5.2,speed:.27,duration:7},{clip:'return-v17.mp4',start:7.9,speed:.18,duration:6},{clip:'correction-v19.mp4',start:3.4,speed:.26,duration:5}]} compactCaption label="Real rejection and correction"/>],
  [146,14,<MixedOutcomesShot/>],
  [160,14,<UIShot index="13" title="Circle wallets · Arc settlement" footer="Always-on programmable USDC, with a receipt tied to the invoice" clip="arc-proof.mp4" start={0} speed={.9} compactCaption label="Separate historical Testnet verification" test/>],
  [174,11,<UIShot index="14" title="Verifiable on Arc Explorer" footer="50 workflow tests · 40 confirmed Testnet transfers of 0.01 USDC" clip="arc-proof.mp4" start={38} speed={.36} zoom={1} zoomEnd={1.08} motionFrames={330} origin="45%" originY="36%" compactCaption label="Historical transaction · not the filmed request" test/>],
  [185,7,<Outro/>]
];
export const Film:React.FC<{withBgm:boolean}>=({withBgm})=><AbsoluteFill style={base}>
  {shots.map(([from,duration,node],i)=><Sequence key={i} from={from*FPS} durationInFrames={duration*FPS}>{node}</Sequence>)}
  {shots.map(([from,duration],i)=><Sequence key={'vo'+i} from={from*FPS} durationInFrames={duration*FPS}><Audio src={staticFile('media/vo-'+String(i+1).padStart(2,'0')+'.mp3')} volume={.92}/></Sequence>)}
  {withBgm&&<Loop durationInFrames={3343}><Audio src={staticFile('media/house-vibez.mp3')} volume={.095}/></Loop>}
</AbsoluteFill>;
