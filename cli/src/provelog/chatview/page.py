"""The viewer page: one self-contained document, read-only.

Layout:

    [ sidebar 272 | conversation <= 800 | drawer 320 (on demand) ]

The sidebar mirrors DSH: brand + agent nav, Workspaces header, workspace tree,
and the conversation count / export footer. The
conversation owns the screen; everything technical (paths, ids, coverage)
lives in the drawer that opens on demand. No composer: this is a record, not
a chat - the composer's seat carries the read-only facts instead.

Everything is inline (no font, image or script may leave the page: the
app's CSP is default-src 'none'). The markdown subset deliberately has no
links and no images, so transcript text can never trigger a request.
"""

from __future__ import annotations

import json

from .agent_icons import AGENT_ICONS

PAGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>ProveLog</title>
<link rel="shortcut icon" href="/favicon.ico?v=4">
<link rel="icon" href="/favicon.ico?v=4" sizes="any">
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=4">
<link rel="icon" type="image/png" sizes="64x64" href="/favicon.png?v=4">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png?v=4">
<script>
(function(){try{var p=localStorage.getItem("provelog-theme")||"auto";
var d=p==="auto"?(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):p;
document.documentElement.dataset.theme=d;}catch(e){}})();
</script>
<style>
:root{
color-scheme:light;
--bg:#FFFFFF;--side:#F4F7F8;--l1:#FFFFFF;--l2:#EEF3F5;--l3:#E1E9EC;
--bd:rgba(11,20,24,.07);--bd2:rgba(11,20,24,.11);--bd3:rgba(11,20,24,.19);
--tx:#0B1418;--tx2:#3E5058;--tx3:#74878F;--cap:#9FB0B7;
--hover:rgba(11,20,24,.05);--active:rgba(11,20,24,.09);
--fill:#0E8F7E;--fill-fg:#FFFFFF;--fill-hover:#0B7A6C;
--bubble:rgba(14,143,126,.07);--code:#F4F7F8;--accent:#0E8F7E;--accent-bg:rgba(14,143,126,.07);
--ok:#12A150;--ok-text:#0C7A3C;--ok-bg:rgba(18,161,80,.10);--ok-bd:rgba(18,161,80,.28);
--warn:#C98209;--warn-text:#8A5A06;--warn-bg:rgba(201,130,9,.12);--warn-bd:rgba(201,130,9,.3);
--err:#D93A33;--err-text:#B02A25;--err-bg:rgba(217,58,51,.07);
--logo-chev:#0E8F7E;--dot-off:#C9D5DA;
--shadow:0 0 1px 0 rgba(11,20,24,.2),0 0 4px 0 rgba(11,20,24,.02),0 12px 32px 0 rgba(11,20,24,.08);
--shadow-soft:0 1px 2px 0 rgba(0,0,0,.04);
}
[data-theme="dark"]{
color-scheme:dark;
--bg:#070C10;--side:#0C1419;--l1:#0F191F;--l2:#131F26;--l3:#1A2830;
--bd:rgba(231,240,242,.07);--bd2:rgba(231,240,242,.11);--bd3:rgba(231,240,242,.20);
--tx:#E7F0F2;--tx2:#A4B6BD;--tx3:#7C929B;--cap:#56696F;
--hover:rgba(231,240,242,.07);--active:rgba(231,240,242,.12);
--fill:#5EE0C8;--fill-fg:#04120F;--fill-hover:#7BE8D4;
--bubble:rgba(94,224,200,.08);--code:#050A0D;--accent:#5EE0C8;--accent-bg:rgba(94,224,200,.08);
--ok:#34D399;--ok-text:#34D399;--ok-bg:rgba(52,211,153,.12);--ok-bd:rgba(52,211,153,.3);
--warn:#FBBF24;--warn-text:#FBBF24;--warn-bg:rgba(251,191,36,.12);--warn-bd:rgba(251,191,36,.3);
--err:#F87171;--err-text:#F87171;--err-bg:rgba(248,113,113,.10);
--logo-chev:#5EE0C8;--dot-off:#3A4A51;
--shadow:0 0 1px 0 rgba(0,0,0,.6),0 0 4px 0 rgba(0,0,0,.2),0 12px 32px 0 rgba(0,0,0,.4);
--shadow-soft:0 1px 2px 0 rgba(0,0,0,.3);
}
*{box-sizing:border-box}
.hidden{display:none !important}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--tx);overflow:hidden;
  font:13px/20px Inter,-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
button{font:inherit;color:inherit;background:none;border:none;cursor:pointer}
#app{display:flex;height:100%}

#side{width:272px;flex:0 0 272px;background:var(--side);display:flex;flex-direction:column;height:100%}
.side-top{height:230px;flex:0 0 230px;display:flex;flex-direction:column;min-height:0}
.brandrow{height:64px;flex:0 0 64px;padding:14px 14px 6px;display:flex;align-items:center;gap:7px}
.brandrow .logo{font-size:16px}
.agents{flex:1;min-height:0;overflow:auto;padding:2px 8px 8px;
  scrollbar-width:none}
.agents::-webkit-scrollbar{width:0;height:0}
.agent{display:flex;align-items:center;gap:8px;width:100%;height:28px;padding:0 8px;
  border-radius:7px;color:var(--tx2);font-size:13px;text-align:left;white-space:nowrap}
.agent:hover{background:var(--hover);color:var(--tx)}
.agent.on{background:var(--active);color:var(--tx);font-weight:500}
.agent .agentlogo{width:16px;height:16px;border-radius:4px;object-fit:cover;flex:none}
[data-theme="dark"] .agentlogo.glyph{filter:invert(1)}
[data-theme="dark"] .agentlogo.tile{box-shadow:0 0 0 .5px rgba(231,240,242,.45)}
.agent .agentmono{width:16px;height:16px;border-radius:4px;background:var(--l3);color:var(--tx3);
  display:grid;place-items:center;font-size:9px;font-weight:700;flex:none}
.agent .an{min-width:0;overflow:hidden;text-overflow:ellipsis}
.agent .ac{margin-left:auto;font-size:10px;color:var(--cap)}
.workspaces-head{height:65px;flex:0 0 65px;padding:20px 8px 8px 14px;display:flex;
  align-items:center;gap:2px}
.workspaces-head .wtitle{font-size:11px;line-height:14px;color:var(--cap);letter-spacing:.01em}
.workspaces-head .sp{flex:1}
#searchbtn,#sortbtn{width:26px;height:26px;border-radius:6px;color:var(--tx3)}
#searchbtn:hover,#sortbtn:hover{background:var(--hover);color:var(--tx)}
#searchbtn svg,#sortbtn svg{width:15px;height:15px;fill:none;stroke:currentColor;
  stroke-width:1.25;stroke-linecap:round;stroke-linejoin:round}
#searchbtn.on{background:var(--active);color:var(--tx)}
.searchpanel{position:absolute;z-index:10;top:290px;left:8px;right:8px;
  padding:3px 4px;border-radius:8px;background:var(--side);
  box-shadow:var(--shadow),0 0 0 .5px var(--bd2);display:flex;align-items:center;gap:4px}
.searchpanel .ib.sm{flex:none;color:var(--cap)}
.searchpanel .ib.sm:hover{color:var(--tx)}
#side{position:relative}

.ib.sm{width:26px;height:26px;border-radius:6px;color:var(--cap)}
.ib.sm:hover{background:var(--hover);color:var(--tx)}
.ib.sm svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.3;
  stroke-linecap:round;stroke-linejoin:round}
.lang-switcher{position:relative;display:flex}
.flat{display:inline-flex;align-items:center;gap:5px;height:28px;padding:0 6px;border-radius:6px;
  color:var(--tx3);font-size:12px;white-space:nowrap}
.flat:hover{background:var(--hover);color:var(--tx)}
.flat svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.3;
  stroke-linecap:round;stroke-linejoin:round}
.flat .chev{width:12px;height:12px;opacity:.7;transition:transform .18s}
.flat[aria-expanded="true"] .chev{transform:rotate(180deg)}
.acts .sep{width:.5px;height:14px;background:var(--bd2);margin:0 4px}
.lang-dropdown{position:absolute;top:calc(100% + 6px);right:0;width:188px;padding:4px;
  border-radius:12px;background:var(--l1);box-shadow:var(--shadow),0 0 0 .5px var(--bd2);
  z-index:25;opacity:0;visibility:hidden;transform:translateY(4px);
  transition:opacity .14s,transform .14s,visibility .14s}
.lang-dropdown.open{opacity:1;visibility:visible;transform:none}
.lang-item{display:flex;align-items:center;gap:8px;width:100%;padding:7px 8px;border-radius:8px;
  text-align:left}
.lang-item:hover{background:var(--hover)}
.lang-item.on{background:var(--active)}
.lang-item .names{display:flex;flex-direction:column;min-width:0;line-height:16px}
.lang-item .n{font-size:13px;font-weight:500;color:var(--tx)}
.lang-item .e{font-size:11px;color:var(--cap)}
.lang-item .ck{width:13px;height:13px;margin-left:auto;flex:0 0 13px;color:var(--tx2);
  fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;
  opacity:0}
.lang-item.on .ck{opacity:1}
.logo{font-family:-apple-system,"SF Pro Display","SF Pro Text",BlinkMacSystemFont,sans-serif;
  font-size:17px;font-weight:600;letter-spacing:-.021em;color:var(--tx);
  display:inline-flex;align-items:center;gap:.14em;line-height:1;
  text-decoration:none;cursor:pointer;border-radius:4px}
.logo:hover{opacity:.8}
.logo:focus-visible{outline:2px solid var(--bd3);outline-offset:2px}
.logo .cv{width:.528em;height:1.01em;flex:none;display:block;position:relative;top:-.012em;
  color:var(--logo-chev)}
.logo .cv path{fill:none;stroke:currentColor;stroke-width:16;
  stroke-linecap:butt;stroke-linejoin:miter}
#q{width:100%;min-width:0;flex:1;height:30px;border-radius:8px;background:var(--l3);border:none;
  color:var(--tx);padding:0 10px;font:13px/20px inherit;outline:none}
[data-theme="dark"] #q{background:var(--l2)}
#q::placeholder{color:var(--cap)}
#q:focus{background:var(--l1);box-shadow:0 0 0 1.5px var(--bd2)}
#list{flex:1;overflow:auto;padding:0 8px 8px;overscroll-behavior:contain;
  scrollbar-width:thin;scrollbar-color:var(--bd2) transparent}
#list::-webkit-scrollbar{width:5px;height:5px}
#list::-webkit-scrollbar-thumb{background:var(--bd2);border-radius:999px}
#list::-webkit-scrollbar-track{background:transparent}
.gtitle{font-size:11px;line-height:14px;color:var(--cap);padding:14px 8px 6px}
.folder{display:flex;align-items:center;gap:8px;padding:10px 8px 5px;cursor:pointer;
  color:var(--tx2);font-size:12px;line-height:17px;border-radius:7px}
.folder:hover{background:var(--hover)}
.folder .fico{width:15px;height:15px;flex:none;fill:none;stroke:currentColor;stroke-width:1.2;
  color:var(--tx3)}
.folder.on .fico{color:var(--accent)}
.folder .fname{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  font-weight:500}
.item{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto auto;
  gap:6px;align-items:center;padding:7px 7px 7px 23px;border-radius:8px;cursor:pointer}
.item:hover{background:var(--hover)}
.item.on{background:var(--active)}
.item.arch .t{color:var(--tx3)}
.item:focus-visible{outline:none;background:var(--hover);box-shadow:inset 0 0 0 1.5px var(--bd3)}
.item .t{font-size:13px;line-height:20px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item.on .t{font-weight:500}
.item .meta{display:inline-flex;align-items:center;gap:4px;justify-content:flex-end;min-width:0}
.item .time{font-size:11px;line-height:16px;color:var(--cap);white-space:nowrap}
.item .rowagent{position:absolute;left:3px;top:50%;transform:translateY(-50%);
  width:16px;height:16px;display:flex;align-items:center;justify-content:center;
  pointer-events:none;opacity:.9;overflow:hidden;border-radius:4px}
.item .rowagent .agentlogo,.item .rowagent .agentmono{width:16px;height:16px;
  border-radius:4px;object-fit:cover}
.item .rowagent .agentmono{display:flex;align-items:center;justify-content:center;
  background:var(--l3);color:var(--tx3);font-size:9px;font-weight:600}
.item .acts{display:none;align-items:center;gap:1px;margin-right:-4px}
.item:hover .acts,.item:focus-within .acts,.item.menu-open .acts{display:flex}
.item:hover .meta,.item:focus-within .meta,.item.menu-open .meta{display:none}
.item .act{width:24px;height:24px;border-radius:7px;display:grid;place-items:center;
  color:var(--tx3);flex:none}
.item:hover .act{color:var(--tx2)}
.item .act svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.8;
  stroke-linecap:round;stroke-linejoin:round}
.item .act .dot{fill:currentColor;stroke:none}
.item .act:hover{background:var(--l3);color:var(--tx)}
.more-row{display:block;width:100%;margin:2px 0 2px 21px;padding:5px 8px;border-radius:6px;
  color:var(--tx3);font-size:12px;text-align:left;background:transparent}
.more-row:hover{background:var(--hover);color:var(--tx2)}
#sidefoot{height:62px;flex:0 0 62px;border-top:.5px solid var(--bd);
  padding:0 12px;display:flex;align-items:center;gap:7px;font-size:12px;color:var(--tx2)}
#sidefoot .sp{flex:1}
.accounticon{width:24px;height:24px;border-radius:50%;background:var(--l2);display:grid;
  place-items:center;color:var(--tx3);flex:none}
.accounticon svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.25;
  stroke-linecap:round;stroke-linejoin:round}
#side{position:relative}

#main{flex:1;min-width:0;display:flex;flex-direction:column;position:relative;
  transition:padding-right .16s ease;background:var(--bg)}
#top{height:80px;flex:0 0 80px;display:flex;align-items:center;gap:10px;
  padding-left:52px;padding-right:24px}
#top.hidden{display:none}
#top.bare .h,#top.bare .session-only,#top.bare .viewtabs{display:none}
#top.bare .acts .sep{display:none}
.h{min-width:0}
#title{font-size:14px;line-height:22px;font-weight:600;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
#meta{font-size:11px;line-height:14px;color:var(--cap);margin-top:1px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
#meta .intg.ok{color:var(--ok-text)}
#meta .intg.bad{color:var(--err-text)}

.viewtabs{display:flex;align-items:center;gap:2px;margin-top:3px}
.viewtabs button{height:20px;padding:0 7px;border-radius:5px;font-size:11px;
  line-height:14px;color:var(--cap);background:none;border:0;cursor:pointer}
.viewtabs button:hover{color:var(--tx2);background:var(--hover)}
.viewtabs button.on{color:var(--tx);background:var(--active)}
#trajectory{flex:1;min-height:0;overflow:auto;padding:0 0 24px}
.trajbar{position:sticky;top:0;z-index:3;display:flex;align-items:center;gap:10px;
  min-height:34px;padding:0 22px;border-bottom:.5px solid var(--bd);
  background:var(--bg);font-size:11px;color:var(--cap)}
.trajbar .sp{flex:1}
.trajsearch{width:160px;height:22px;border:.5px solid var(--bd2);border-radius:5px;
  background:var(--l1);color:var(--tx2);font:inherit;font-size:11px;padding:0 7px;outline:none}
.trajsearch:focus{border-color:var(--bd3);background:var(--bg2)}
.trajbar button{color:var(--cap);font-size:11px;background:none;border:0;cursor:pointer;
  padding:3px 6px;border-radius:4px}
.trajbar button:hover{color:var(--tx);background:var(--hover)}
.trajtimeline{position:relative;height:52px;margin:10px 22px 0;border:.5px solid var(--bd);
  border-radius:7px;background:var(--l1);overflow:hidden}
.trajlanes{position:absolute;left:44px;right:10px;top:0;bottom:0}
.trajtrack{position:absolute;left:0;right:0;height:8px}
.trajtrack.l0{top:9px}.trajtrack.l1{top:23px}.trajtrack.l2{top:37px}
.trajtrack.l2{top:37px}
.trajtrack .tl{position:absolute;height:8px;min-width:2px;border-radius:2px;
  background:var(--cap);opacity:.75}
.trajtimeline .tl.user{background:#3b82f6}
.trajtimeline .tl.assistant{background:#8b5cf6}
.trajtimeline .tl.tool{background:#d97706}
.trajtimeline .tl.error{background:var(--err)}
.trajtimeline .tlabel{position:absolute;left:8px;font-size:9px;color:var(--cap);
  line-height:10px}
.trajtimeline .tlabel.a{top:7px}.trajtimeline .tlabel.b{top:21px}.trajtimeline .tlabel.c{top:35px}
.trajaxis{display:flex;justify-content:space-between;margin:4px 22px 8px;
  font-size:9px;color:var(--cap)}
.trajhead{display:grid;grid-template-columns:82px minmax(0,1fr) 74px 92px;
  gap:8px;align-items:center;height:28px;margin:8px 22px 0;padding:0 4px;
  border-top:.5px solid var(--bd);border-bottom:.5px solid var(--bd);
  color:var(--cap);font-size:10px;font-weight:500}
.trajhead span:nth-child(3),.trajhead span:nth-child(4){text-align:right}
.trajledger{margin:0 22px}
.trajturn{position:sticky;top:34px;z-index:2;display:flex;align-items:center;gap:6px;
  height:26px;padding:0 4px;border-bottom:.5px solid var(--bd);background:var(--bg);
  color:var(--cap);font-size:10px;font-family:ui-monospace,SFMono-Regular,Monaco,monospace}
.trajturn .tchev{width:12px;color:var(--cap)}
.trajturn .tcount{margin-left:auto;color:var(--cap)}
.trajstep{display:flex;align-items:center;gap:6px;padding:5px 4px 4px 22px;
  color:var(--cap);font-size:10px;font-family:ui-monospace,SFMono-Regular,Monaco,monospace}
.trajrow{display:grid;grid-template-columns:82px minmax(0,1fr) 74px 92px;
  gap:8px;align-items:baseline;min-height:30px;padding:5px 4px;
  border-bottom:.5px solid var(--l2);font-size:12px;line-height:18px}
.trajrow:hover{background:var(--hover)}
.trajrow .tk{font-size:9px;font-weight:650;letter-spacing:.03em;line-height:17px;
  text-align:center;border-radius:3px;padding:0 4px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.trajrow .tk.user{color:#2563eb;background:#dbeafe}
.trajrow .tk.assistant{color:#7c3aed;background:#ede9fe}
.trajrow .tk.tool{color:#9a5b00;background:#fef3c7}
.trajrow .tk.compaction{color:var(--cap);background:var(--l2)}
.trajrow .tc{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  cursor:pointer}
.trajrow .tc.tool{font-family:ui-monospace,SFMono-Regular,Monaco,monospace;
  font-size:11px;color:var(--tx2)}
.trajrow .tm,.trajrow .tt{font-size:10px;color:var(--cap);white-space:nowrap;
  text-align:right;font-variant-numeric:tabular-nums}
.trajrow.open .tc{white-space:normal}
.trajdetail{grid-column:1 / -1;margin:2px 0 6px 90px;padding:8px 10px;
  border-radius:6px;background:var(--l1);border:.5px solid var(--l2);
  font:11px/17px ui-monospace,SFMono-Regular,Monaco,monospace;
  white-space:pre-wrap;overflow-wrap:anywhere;max-height:340px;overflow:auto}
.trajempty{color:var(--cap);font-size:12px;padding:30px 22px}
@media (max-width:760px){.trajrow{grid-template-columns:60px minmax(0,1fr) 54px}
  .trajrow .tt{display:none}.trajledger{margin:0 12px}.trajbar{padding:0 12px}
  .trajtimeline{margin-left:12px;margin-right:12px}.trajaxis{margin-left:12px;margin-right:12px}}
.acts{margin-left:auto;display:flex;align-items:center;gap:6px}
.kidmenu{position:absolute;top:52px;right:max(24px, calc((100% - 720px) / 2 + 32px));
  z-index:60;width:336px;max-width:min(400px,calc(100vw - 32px));max-height:min(560px,70vh);
  overflow:auto;padding:4px;border-radius:10px;background:var(--bg2);
  box-shadow:0 10px 34px rgba(0,0,0,.18),0 0 0 .5px var(--bd2)}
.kidmenu.hidden{display:none}
.kidrow{display:flex;align-items:flex-start;gap:7px;width:100%;padding:6px 7px;
  border-radius:7px;background:none;border:0;text-align:left;cursor:pointer;
  color:var(--tx);font:inherit;font-size:12px;line-height:17px}
.kidrow:hover,.kidrow:focus-visible{background:var(--hover)}
.kidrow .kdisclosure{flex:none;width:12px;min-height:17px;color:var(--cap);
  font-size:10px;line-height:17px;text-align:center;cursor:pointer}
.kidrow .kdot{flex:none;width:7px;height:7px;margin-top:5px;border-radius:50%;
  background:var(--cap)}
.kidrow .kdot.ok{background:var(--ok)} .kidrow .kdot.warn{background:var(--warn)}
.kidrow .kbody{min-width:0;flex:1;display:flex;flex-direction:column}
.kidrow .klabel{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kidrow .kmeta{color:var(--cap);font-size:10px;line-height:15px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.kidrow .kcount{flex:none;color:var(--cap);font-size:10px;line-height:17px}
.kidbranch{display:flex;flex-direction:column}
.btn{height:28px;padding:0 10px;border-radius:6px;background:transparent;color:var(--tx2);
  font-size:13px;display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.btn:hover{background:var(--hover)}
.btn.pri{background:var(--fill);color:var(--fill-fg)}
.btn.pri:hover{background:var(--fill-hover);color:var(--fill-fg)}
.btn.danger{color:var(--err-text);background:transparent}
.btn.danger:hover{background:var(--err-bg)}
.btn:disabled,.btn[disabled]{opacity:.4;cursor:default;pointer-events:none}
.ib{width:28px;height:28px;border-radius:6px;display:grid;place-items:center;color:var(--tx3)}
.ib:hover{background:var(--hover);color:var(--tx)}
.ib.danger:hover{background:var(--err-bg);color:var(--err-text)}
#flow{flex:1;overflow:auto;padding:16px 0 8px;overscroll-behavior:contain;
  scrollbar-gutter:stable;scrollbar-width:thin;scrollbar-color:var(--bd3) transparent}
#flow::-webkit-scrollbar{width:5px;height:5px}
#flow::-webkit-scrollbar-track{background:transparent;margin:0}
#flow::-webkit-scrollbar-thumb{background:var(--bd3);border-radius:999px;border:0}
#flow::-webkit-scrollbar-thumb:hover{background:var(--tx3)}
#flow{scroll-behavior:smooth}
#turnrail{position:absolute;top:80px;right:14px;bottom:36px;width:28px;z-index:4;
  display:flex;justify-content:center;padding:28px 0;pointer-events:none}
#turnmarks{position:relative;width:20px;height:100%;pointer-events:auto}
.turnmark{position:absolute;right:0;width:10px;height:1.5px;padding:0;border:0;
  background:var(--bd3);border-radius:2px;cursor:pointer;pointer-events:auto}
.turnmark:hover{width:18px;background:var(--tx3)}
.turnmark.on{width:20px;height:2px;background:var(--tx)}
@media (max-width:980px){#turnrail{display:none}}
.col{max-width:748px;margin:0 auto;padding:0}
.banner{background:var(--warn-bg);color:var(--warn-text);border-radius:8px;padding:8px 12px;
  font-size:12px;line-height:18px;margin-bottom:18px}
.msg{margin-bottom:26px}
.msg.user{display:flex;justify-content:flex-end}
.msg.user{min-width:0}
.msg.user .bubblewrap{display:flex;flex-direction:column;align-items:flex-end;gap:6px;
  max-width:70%;min-width:0}






.msg.user .bubble{background:var(--bubble);border-radius:18px 18px 4px 18px;
  padding:10px 16px;max-width:100%;min-width:0;white-space:normal;
  overflow-wrap:anywhere;word-break:break-word;font-size:16px;line-height:26px}
.msg.user .bubble>:first-child{margin-top:0}
.msg.user .bubble>:last-child{margin-bottom:0}
.msg.user .bubble p{margin:0 0 10px}
.msg.user .bubble h1,.msg.user .bubble h2,.msg.user .bubble h3,.msg.user .bubble h4{
  margin:14px 0 6px;font-size:16px;line-height:26px;font-weight:600}
.msg.user .bubble ul,.msg.user .bubble ol{margin:0 0 10px;padding-left:20px}
.msg.user .bubble li{margin:2px 0}
.msg.user .bubble blockquote{margin:0 0 8px;padding:2px 0 2px 12px;
  border-left:2px solid var(--bd2);color:var(--tx3)}
.msg.user .bubble hr{border:none;border-top:.5px solid var(--bd2);margin:12px 0}
.msg.user .bubble code{background:var(--l1);border-radius:4px;padding:1px 5px;
  font:12px/19px ui-monospace,SFMono-Regular,monospace}
.msg.user .bubble .codeblock{max-width:100%;overflow:hidden;background:var(--code);
  border:.5px solid var(--bd);border-radius:12px;margin:8px 0}
.msg.user .bubble .codeblock pre{max-width:100%;overflow:auto;white-space:pre;
  margin:0;padding:10px 14px}
.msg.user .bubble .codeblock pre code{background:none;padding:0;font-size:12.5px;line-height:20px}
.msg.user .bubble table{display:block;max-width:100%;overflow:auto;border-collapse:collapse;
  margin:0 0 10px;font-size:13px;line-height:20px}
.msg.user .bubble th,.msg.user .bubble td{border-bottom:.5px solid var(--bd2);
  padding:6px 10px;text-align:left;vertical-align:top}
.imgs{display:flex;flex-wrap:wrap;gap:10px;margin:6px 0;max-width:100%}
.toolgallery{margin:7px 0 9px;padding-left:22px}
.msg.user .imgs{justify-content:flex-end;margin:0}
.msg.user .files{display:flex;flex-direction:column;align-items:flex-end;gap:4px;max-width:100%}
.msg.user .filerow{display:flex;align-items:center;gap:8px;max-width:100%;min-width:0;padding:6px 10px;
  border-radius:10px;background:var(--l2);font-size:12px;line-height:18px;color:var(--tx2)}
.msg.user .filerow .fn{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500;color:var(--tx)}
.msg.user .filerow .fm{flex:none;color:var(--tx3)}
.shotlink{display:inline-block;line-height:0;border-radius:8px}
.shot{max-width:240px;max-height:240px;border-radius:12px;background:var(--l2);
  display:block;object-fit:contain;cursor:zoom-in;box-shadow:0 0 0 .5px var(--bd2)}
.shot.broken{display:none}
.shot.tile{width:64px;height:64px;min-width:64px;min-height:64px;object-fit:cover}
.mdimg{max-width:240px;max-height:240px;border-radius:12px;margin:8px 0;cursor:zoom-in}
.imgmiss{font-size:12px;line-height:18px;color:var(--cap);border:.5px dashed var(--bd2);
  border-radius:6px;padding:4px 8px}
.wrapcard{max-width:640px;margin:14px 0 6px;padding:14px 16px;border:.5px solid var(--bd2);
  border-radius:10px;background:var(--l1);color:var(--tx2);font-size:13px;line-height:21px}
.wrapcard .wrecap{white-space:pre-wrap}
.wrapcard .wask{margin-top:12px;color:var(--tx);font-size:14px;line-height:22px;white-space:pre-wrap}
.wrapcard .wacts{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.wrapcard .wact{font-size:12px;line-height:18px;padding:3px 9px;border-radius:7px;
  border:.5px solid var(--bd2);background:var(--l2);color:var(--tx2)}
.wrapcard .wact.on{background:var(--l3);border-color:var(--bd3);color:var(--tx)}

.lb{position:fixed;inset:0;z-index:1000}
.lb[hidden]{display:none}
.lbs{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:40px}
.lbmask{position:absolute;inset:0;background:rgba(0,0,0,.62);backdrop-filter:blur(10px)}
.lbimg{position:relative;max-width:min(100%,1600px);max-height:100%;object-fit:contain;
  border-radius:12px;background:var(--l2);box-shadow:0 24px 80px rgba(0,0,0,.35)}
.lbclose{position:absolute;top:20px;right:20px;z-index:1;width:36px;height:36px;
  border-radius:50%;border:.5px solid var(--bd2);background:var(--l1);color:var(--tx);
  display:grid;place-items:center;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.16)}
.lbclose:hover{background:var(--l3)}
.lbclose svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;
  stroke-linecap:round}
body.lb-open{overflow:hidden}
@media (max-width:600px){.lbs{padding:16px}.lbclose{top:12px;right:12px}}

.msg.ai .body{font-size:16px;line-height:26px;word-break:break-word}
.msg.ai .body p{margin:0 0 12px}
.msg.ai .body h1,.msg.ai .body h2,.msg.ai .body h3,.msg.ai .body h4{
  margin:20px 0 8px;font-size:18px;line-height:28px;font-weight:600}
.msg.ai .body h3,.msg.ai .body h4{font-size:16px;line-height:26px}
.msg.ai .body ul,.msg.ai .body ol{margin:0 0 10px;padding-left:20px}
.msg.ai .body li{margin:2px 0}
.msg.ai .body blockquote{margin:0 0 8px;padding:2px 0 2px 12px;
  border-left:2px solid var(--bd2);color:var(--tx3)}
.msg.ai .body hr{border:none;border-top:.5px solid var(--bd2);margin:14px 0}
.msg.ai .body code{background:var(--l2);border-radius:4px;padding:1px 5px;
  font:12px/19px ui-monospace,SFMono-Regular,monospace}
.msg.ai .body .codeblock{background:var(--code);border-radius:12px;overflow:hidden;
  margin:0 0 12px;border:.5px solid var(--bd)}
.codehead{display:flex;align-items:center;gap:8px;padding:6px 10px;color:var(--tx3);
  font-size:11px;line-height:18px;border-bottom:.5px solid var(--bd)}
.codehead .sp{flex:1}
.codehead .copybtn{width:22px;height:22px;color:var(--cap)}
.codehead .copybtn:hover{background:var(--hover);color:var(--tx)}
.copybtn.copied{color:var(--ok);background:var(--hover)}
.msg.ai .body .codeblock pre{background:transparent;border-radius:0;margin:0;padding:10px 14px}
.msg.ai .body .codeblock pre code{background:none;padding:0;font-size:12.5px;line-height:20px}
.msg.ai .body pre{background:var(--code);border-radius:8px;padding:10px 12px;overflow:auto;
  margin:0 0 10px}
.msg.ai .body pre code{background:none;padding:0;font-size:12.5px;line-height:20px}
.msg.ai .body table{border-collapse:collapse;margin:0 0 12px;font-size:13px;line-height:20px;
  display:block;max-width:100%;overflow:auto}
.msg.ai .body th,.msg.ai .body td{border:none;border-bottom:.5px solid var(--bd);
  padding:7px 12px;text-align:left;vertical-align:top}
.msg.ai .body th{border-bottom-color:var(--bd2);color:var(--tx2);font-weight:500;
  white-space:nowrap}
.msg.ai .body td{color:var(--tx3)}
.usage{color:var(--cap);font-size:11px;line-height:14px;margin-top:4px}
.msg-actions{display:flex;align-items:center;justify-content:flex-end;gap:6px;min-height:22px;
  color:var(--cap);font-size:10px;line-height:16px}
.msg.ai .msg-actions{justify-content:flex-start}
.copybtn{width:22px;height:22px;border-radius:5px;display:inline-grid;place-items:center;
  color:var(--cap);flex:none}
.copybtn:hover{background:var(--hover);color:var(--tx)}
.copybtn svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.25;
  stroke-linecap:round;stroke-linejoin:round}
.toolchev{width:12px;flex:none;color:var(--cap);font-size:14px;line-height:16px}
.turnproc-toggle{display:flex;align-items:center;gap:8px;width:100%;min-height:24px;
  padding:0;border:0;background:none;color:var(--tx3);font:inherit;font-size:13px;
  line-height:24px;text-align:left;cursor:pointer}
.turnproc-toggle .chev{display:inline-block;color:var(--cap);transition:transform .15s}
.turnproc-toggle[aria-expanded="true"] .chev{transform:rotate(90deg)}
.turnproc-toggle:hover{color:var(--tx2)}
.turnstatus{font-size:13px;line-height:24px;color:var(--tx3);margin:2px 0 8px}
.turnstatus.failed{color:var(--err-text)}
.procseg{margin:2px 0 10px}
.procseg[hidden]{display:none}
.stepgroup{margin:2px 0 8px}
.stepgroup>summary{list-style:none;display:flex;align-items:center;gap:8px;
  min-height:24px;color:var(--tx3);font-size:13px;line-height:24px;cursor:pointer}
.stepgroup>summary::-webkit-details-marker{display:none}
.stepgroup>summary .chev{display:inline-block;color:var(--cap);transition:transform .15s}
.stepgroup[open]>summary .chev{transform:rotate(90deg)}
.stepgroup>summary:hover{color:var(--tx2)}
.stepgroup .stepbody{padding:2px 0 2px;max-height:min(400px,50vh);overflow-y:auto;
  overscroll-behavior-y:auto;scrollbar-gutter:stable}
.stepgroup .proc-text{font-size:14px;line-height:22px;color:var(--tx3);margin:0 0 10px}
.row{margin:6px 0}
.row>summary{list-style:none;display:flex;align-items:center;gap:6px;padding:2px 0;
  color:var(--tx3);font-size:13px;line-height:20px;cursor:pointer}
.row>summary::-webkit-details-marker{display:none}
.row>summary:hover{color:var(--tx2)}
.row>summary .k{position:relative;width:16px;height:16px;flex:0 0 16px;
  display:inline-flex;align-items:center;justify-content:center;color:inherit}
.row>summary .k svg{width:14px;height:14px;fill:none;stroke:currentColor;
  stroke-width:1.2;stroke-linecap:round;stroke-linejoin:round}
.row>summary .k .chev{position:absolute;inset:0;display:flex;align-items:center;
  justify-content:center;opacity:0;transition:opacity .1s;font-size:13px}
.row>summary .s{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  color:inherit}
.row>summary .n{flex:0 0 auto;color:var(--cap);font-size:11px}
.row>summary:hover .ico,.row[open]>summary .ico{opacity:0}
.row>summary:hover .chev,.row[open]>summary .chev{opacity:1}
.row>summary .ico{position:absolute;inset:0;display:flex;align-items:center;
  justify-content:center;transition:opacity .1s}
.row[open]>summary{padding-bottom:4px}
.row .think,.row>pre,.row .tools{padding-left:22px}
.row pre{margin:2px 0 6px 22px;padding:8px 10px;border:none;border-radius:8px;
  background:var(--code);font:12px/19px ui-monospace,SFMono-Regular,monospace;
  color:var(--tx2);white-space:pre-wrap;word-break:break-word;max-height:420px;overflow:auto}
.row .think{padding:6px 0 6px 22px;font-size:13px;line-height:21px;color:var(--tx3);
  max-height:420px;overflow:auto}
.row .think p{margin:0 0 8px}
.row .tools{padding:2px 0 2px 22px}
.tool summary{list-style:none;display:flex;align-items:center;gap:7px;padding:3px 6px;
  border-radius:6px;cursor:pointer;color:var(--tx3);font-size:12px;line-height:20px}
.tool summary::-webkit-details-marker{display:none}
.tool summary:hover{color:var(--tx2);background:var(--hover)}
.tool .s{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  font-family:ui-monospace,SFMono-Regular,monospace;font-size:11.5px;color:var(--cap)}
.tool .n{flex:0 0 auto;color:var(--cap);font-size:11px}
.tool pre{margin:2px 6px 6px 22px;border-radius:8px;border:none;
  background:var(--code);padding:8px 10px;font:12px/19px ui-monospace,SFMono-Regular,monospace;
  color:var(--tx2);white-space:pre-wrap;word-break:break-word;max-height:320px;overflow:auto}
.tool.err .k{color:var(--err-text)}
.row .out{background:var(--code)}
.row.err .k{color:var(--err-text)}
.pill{height:24px;padding:0 9px;border-radius:6px;font:inherit;font-size:11px;
  line-height:14px;display:inline-flex;align-items:center;gap:6px;background:var(--l2);
  color:var(--tx3);white-space:nowrap;border:none;cursor:pointer;
  max-width:min(260px,34vw);overflow:hidden;text-overflow:ellipsis}
.pill::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;
  flex:none}
.pill.ok{background:var(--ok-bg);color:var(--ok-text)}
.pill.wait{background:var(--warn-bg);color:var(--warn-text)}
.pill.none{background:var(--l2);color:var(--tx3)}
.pill[hidden]{display:none}
.pill:hover{filter:brightness(.97)}



.pill2{display:inline-flex;align-items:center;gap:6px;padding:0;border:none;background:none;cursor:pointer}
.pill2[hidden]{display:none}
.stchip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;line-height:20px;padding:0 8px;
  border-radius:6px;background:var(--l2);color:var(--tx3);white-space:nowrap}
.stchip svg{width:12px;height:12px;flex:none;fill:none;stroke:currentColor;stroke-width:1.5;
  stroke-linecap:round;stroke-linejoin:round}
.stchip .no{width:7px;height:7px;border-radius:50%;box-shadow:inset 0 0 0 1.5px currentColor;flex:none}
.stchip.ok{background:var(--ok-bg);color:var(--ok-text)}
.dochead{margin:12px 0 22px;padding-bottom:16px;border-bottom:.5px solid var(--bd2)}
.dochead h1{font-size:22px;line-height:32px;margin:0 0 6px;letter-spacing:-.01em;font-weight:600;
  overflow-wrap:anywhere}
.dochead .m{color:var(--tx3);font-size:12px;line-height:18px;display:flex;flex-wrap:wrap;gap:4px 14px}
.docsum{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.msg.user{justify-content:flex-start}
.msg.user .bubblewrap{align-items:stretch;max-width:100%;width:100%}
.msg.user .bubble{background:var(--accent-bg);border-left:3px solid var(--accent);
  border-radius:0 10px 10px 0;padding:12px 16px;font-weight:500}
.msg.user .bubble code,.msg.user .bubble pre,.msg.user .bubble table{font-weight:400}
.msg.user .imgs{justify-content:flex-start}
.msg.user .files{align-items:flex-start}
.msg.user .msg-actions{justify-content:flex-start}
.turnsec{position:relative;margin-bottom:34px;scroll-margin-top:12px}
.turnsec>.msg:last-of-type{margin-bottom:0}
.turnsec[data-st]::before{content:"";position:absolute;left:-46px;top:0;bottom:-34px;
  border-left:2px solid var(--ok)}
.turnsec[data-st="none"]::before{border-left:2px dashed var(--bd3)}
.tmark{position:absolute;top:0;right:calc(100% + 60px);display:flex;flex-direction:column;
  align-items:flex-end;gap:2px;padding:1px 0}
.tmark::after{content:"";position:absolute;right:-20.5px;top:5px;width:11px;height:11px;border-radius:50%;
  background:var(--bg);box-shadow:inset 0 0 0 2px var(--ok);transition:transform .2s}
.turnsec[data-st="none"] .tmark::after{box-shadow:inset 0 0 0 2px var(--cap)}
.tmark:hover::after{transform:scale(1.25)}
.turnsec.ev-open .tmark::after{background:var(--tx);box-shadow:none}
.tmark .tno{font-size:10.5px;line-height:15px;color:var(--cap);padding-right:2px}
.tmark:hover .tno{color:var(--tx2)}
.twho{display:flex;align-items:center;gap:8px;font-size:11px;line-height:20px;letter-spacing:.04em;
  color:var(--cap);margin-bottom:6px}
.twho b{font-weight:600;color:var(--tx3)}
.twho .line{flex:1;height:.5px;background:var(--bd)}
.twho .tinl{display:none;padding:0}
.tbound{position:relative;display:flex;align-items:center;gap:8px;margin:-16px 0 18px;padding:4px 0;
  font-size:12px;line-height:18px;color:var(--tx3)}
.tbound.ok{color:var(--ok-text)}
.tbound::before{content:"";position:absolute;left:-46px;top:-4px;bottom:-18px;
  border-left:2px dashed var(--bd3)}
.tbound.ok::before{border-left:2px solid var(--ok)}
.tbound::after{content:"";flex:1;height:.5px;background:currentColor;opacity:.3}
.tbound svg{width:14px;height:14px;flex:none;fill:none;stroke:currentColor;stroke-width:1.3;
  stroke-linecap:round;stroke-linejoin:round}
.tspin{width:11px;height:11px;border-radius:50%;border:1.5px solid currentColor;
  border-right-color:transparent;animation:tspin .8s linear infinite;flex:none}
@keyframes tspin{to{transform:rotate(360deg)}}
.tinfo{margin:12px 0 0;padding:11px 13px;border-radius:10px;background:var(--l2);
  font-size:12.5px;line-height:20px}
.tinfo .th{display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:4px}
.tinfo .th .x{margin-left:auto;color:var(--cap);font-weight:400;font-size:12px;padding:0}
.tfact{display:grid;grid-template-columns:70px minmax(0,1fr);gap:8px;padding:2px 0}
.tfact .k{color:var(--tx3)}
.tfact .v{color:var(--tx2);overflow-wrap:anywhere}
.tfact .v.ok{color:var(--ok-text)}
#outline{position:absolute;top:80px;right:0;bottom:0;width:256px;overflow:auto;z-index:3;
  background:var(--bg);border-left:.5px solid var(--bd);padding:14px 10px 24px 12px;
  scrollbar-width:thin}
#flow.hidden~#outline,#outline:empty{display:none}
#outline .ohead{margin:0 0 6px 8px;font-size:11px;line-height:18px;color:var(--cap)}
#outline a{display:flex;align-items:baseline;gap:6px;padding:4px 8px;margin-top:3px;border-radius:6px;
  color:var(--tx2);font-size:12.5px;line-height:20px;font-weight:500;cursor:pointer}
#outline a:hover{background:var(--hover);color:var(--tx)}
#outline a.on{background:var(--active);color:var(--tx)}
#outline a .n{width:18px;flex:none;color:var(--cap);font-weight:400;font-size:11px}
#outline a .tx{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#outline a .od{flex:none;width:6px;height:6px;border-radius:50%;align-self:center;
  box-shadow:inset 0 0 0 1.5px var(--cap)}
#outline a .od.ok{background:var(--ok);box-shadow:none}
@media (min-width:1001px){#flow{padding-left:190px;padding-right:20px}}
@media (min-width:1241px){
  #flow{padding-right:276px}
  #turnrail{display:none}
  body.drawer-open #flow{padding-right:20px}
  body.drawer-open #outline{display:none}
}
@media (max-width:1240px){#outline{display:none}}
@media (max-width:1000px){
  .tmark,.turnsec[data-st]::before,.tbound::before{display:none}
  .twho .tinl{display:inline-flex}
  .col{padding:0 16px}
  
  #top .pill2{display:none}
}
#empty{flex:1;display:flex;align-items:center;justify-content:center;padding:0 24px}
#empty .box{max-width:380px;text-align:center}
#empty .ic{color:var(--bd3);margin-bottom:14px;display:flex;justify-content:center}
#empty h2{font-size:14px;line-height:22px;margin:0 0 6px;font-weight:600}
#empty p{color:var(--cap);margin:0 0 6px;font-size:12px;line-height:18px}
#empty code{background:var(--l2);border-radius:5px;padding:2px 6px;font-size:11.5px}

#drawer{position:fixed;top:0;right:0;width:320px;height:100%;background:var(--l1);
  box-shadow:-.5px 0 0 var(--bd);transform:translateX(101%);
  transition:transform .18s ease;display:flex;flex-direction:column;z-index:5}
#drawer.open{transform:none}
#drawer .dh{height:52px;flex:0 0 52px;display:flex;align-items:center;padding:0 8px 0 20px;
  font-size:13px;font-weight:500}
#drawer .dh .utc{margin-left:auto;margin-right:10px;font-family:ui-monospace,SFMono-Regular,Monaco,monospace;
  font-size:10px;color:var(--cap);white-space:nowrap;font-variant-numeric:tabular-nums}
#drawer .dh .ib{margin-left:0}
#drawer .dc{flex:1;overflow:auto;padding:2px 20px 24px}
.sect{font-size:11px;line-height:14px;color:var(--cap);margin:20px 0 6px}
.kv{display:flex;justify-content:space-between;gap:12px;padding:3px 0;font-size:13px;
  line-height:20px}
.kv .k{color:var(--tx3);flex:0 0 auto}
.kv .v{color:var(--tx2);text-align:right;overflow-wrap:anywhere;
  max-width:210px;line-height:18px}
.sect2{font-size:11px;line-height:14px;color:var(--cap);margin:14px 0 4px;
  display:flex;align-items:center;gap:6px}
.sect2 .count{margin-left:auto;font-size:10px;font-weight:400;color:var(--cap)}
.stamplist{max-height:126px;overflow:auto;margin:0 -6px;padding:0 6px;
  scrollbar-width:thin;overscroll-behavior:contain}
.stamplist::-webkit-scrollbar{width:5px}
.stamplist::-webkit-scrollbar-thumb{background:var(--bd2);border-radius:999px}
.stamplist:focus-visible{outline:none;box-shadow:inset 0 0 0 1.5px var(--bd3);
  border-radius:6px}
.kv.sub .k{color:var(--cap)}
.pathbox{display:block;width:100%;padding:4px 0;text-align:left}
.pathbox .row1{display:flex;align-items:baseline;gap:10px}
.pathbox .dir{flex:1;font-size:11px;color:var(--cap);overflow-wrap:anywhere;line-height:16px}
.pathbox .z{white-space:nowrap;font-size:11px;color:var(--cap)}
.pathbox .name{display:block;margin-top:1px;font-family:ui-monospace,SFMono-Regular,Monaco,monospace;
  font-size:12px;color:var(--tx2);overflow-wrap:anywhere;line-height:18px}
.pathbox:hover .name{color:var(--tx)}
.row2{display:flex;gap:8px;margin-top:6px}
.row2 .btn{flex:1}
.linkbtn{display:block;width:100%;text-align:center;padding:12px 0 2px;font-size:12px;
  color:var(--cap)}
.linkbtn.danger{color:var(--err-text)}
.linkbtn:hover{color:var(--tx2)}
.filelink{display:inline-flex;align-items:center;gap:5px;max-width:100%;margin:0 1px;
  padding:2px 7px;border:.5px solid var(--bd2);border-radius:6px;background:var(--l2);
  font-family:ui-monospace,SFMono-Regular,Monaco,monospace;font-size:12px;
  color:var(--tx2);cursor:pointer;vertical-align:baseline}
.filelink:hover{background:var(--hover);color:var(--tx);border-color:var(--bd)}
.filelink .fi{width:12px;height:12px;flex:none;fill:none;stroke:currentColor;stroke-width:1.3}
.stampline{display:flex;align-items:baseline;gap:8px;font-size:12px;line-height:20px;
  color:var(--cap);padding:1px 0}
.stampline .p{color:var(--tx3);min-width:54px}
.stampline .c{margin-left:auto}
.stampline .d{color:var(--cap)}
.stampline .now{font-size:10px;background:var(--ok-bg);color:var(--ok-text);
  border-radius:3px;padding:0 4px}
.stampline.cur .p{color:var(--tx)}
.bar{height:3px;border-radius:2px;background:var(--l3);overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:var(--ok)}
.acts2{display:grid;gap:6px;margin-top:20px}
.acts2 .btn{justify-content:center;height:32px;background:var(--l2)}
.acts2 .btn:hover{background:var(--l3)}
.acts2 .btn.pri{background:var(--fill);color:var(--fill-fg)}
.acts2 .btn.pri:hover{background:var(--fill-hover)}
.note{font-size:11.5px;line-height:18px;color:var(--cap);margin-top:12px}

#modal{position:fixed;inset:0;background:rgba(0,0,0,.4);display:none;
  align-items:center;justify-content:center;z-index:20}
#modal.open{display:flex}
#card{width:380px;background:var(--l1);border-radius:14px;
  padding:20px 20px 16px;box-shadow:var(--shadow),0 0 0 .5px var(--bd2)}
#card h3{margin:0 0 6px;font-size:14px;line-height:22px;font-weight:600}
#card p{margin:0 0 6px;color:var(--tx3);font-size:13px;line-height:20px;word-break:break-word}
#card .row-actions{display:flex;justify-content:flex-end;gap:6px;margin-top:18px}
.menu{position:fixed;z-index:40;min-width:176px;padding:4px;border-radius:12px;
  background:var(--l1);box-shadow:var(--shadow),0 0 0 .5px var(--bd2)}
.mi{display:flex;align-items:center;width:100%;padding:7px 9px;border-radius:8px;
  font-size:13px;line-height:20px;color:var(--tx2);text-align:left}
.mi:hover{background:var(--hover);color:var(--tx)}
.mi.danger{color:var(--err-text)}
.mi.danger:hover{background:var(--err-bg);color:var(--err-text)}
#toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);
  background:var(--l1);box-shadow:var(--shadow),0 0 0 .5px var(--bd2);border-radius:10px;
  padding:9px 14px;font-size:13px;line-height:20px;color:var(--tx2);opacity:0;
  transition:opacity .15s;pointer-events:none;z-index:30}
#toast.show{opacity:1}
.drawer-open #main{padding-right:320px}
@media (max-width:1080px){
  .drawer-open #main{padding-right:0}
  #side{width:244px;flex-basis:244px}
  #drawer{width:min(320px,86vw);box-shadow:-24px 0 40px rgba(0,0,0,.18)}
}
@media (max-width:760px){
  #side{display:none}
  .col{padding:0 18px}
}

#top #capture{display:none!important}
#top{height:88px;flex:0 0 88px;padding:10px 24px 10px 32px;border-bottom:1px solid var(--bd2)}
#top .h{flex:1;min-width:100px}
#top .acts{flex:none}
#title{font-size:14px;line-height:22px}
#meta{margin:2px 0 4px}
#top .viewtabs{margin-top:0}
.dochead{display:none}
#flow{padding-top:28px}
#flow .col{max-width:850px}
.msg.user{justify-content:flex-end}
.msg.user .bubblewrap{align-items:flex-end;width:auto;max-width:85%;min-width:0}
.msg.user .bubble{border:0;border-radius:18px;padding:13px 18px;font-weight:400;background:var(--accent-bg);width:100%;box-sizing:border-box}
.msg.user .imgs{justify-content:flex-end}
.msg.user .files{align-items:flex-end}
.msg.user .msg-actions{justify-content:flex-end}
.msg.user .bubble.folded{max-height:none;overflow:visible}
.input-preview{white-space:pre-wrap;overflow-wrap:anywhere;line-height:24px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:9;overflow:hidden;max-height:216px;mask-image:linear-gradient(to bottom,#000 0%,#000 78%,transparent 100%)}
.input-full[hidden],.input-preview[hidden]{display:none!important}
#flow{overflow-anchor:none}
.input-toggle{align-self:flex-start!important;margin-left:0;margin-right:auto;text-align:left}
.btn[hidden]{display:none!important}
.input-toggle{border:0;background:transparent;color:var(--tx3);font:inherit;font-size:12px;padding:4px 2px;cursor:pointer}
.twho{justify-content:flex-end;margin-bottom:8px}
.twho .line{display:none}
.twho b{display:none}
#outline{top:108px;right:16px;bottom:auto;max-height:calc(100% - 140px);width:226px;border:1px solid var(--bd2);border-radius:14px;padding:14px 10px;background:var(--bg)}
@media(min-width:1001px){#flow{padding-left:150px;padding-right:32px}}
@media(min-width:1241px){#flow{padding-left:150px;padding-right:270px}}
@media(max-width:1000px){#top{padding-left:52px}.msg.user .bubblewrap{max-width:94%}}

.agent-line{display:flex;align-items:center;min-width:0}.agent-line>.agent{flex:1;min-width:0}
.scope-more{border:0;background:none;color:var(--tx3);font-size:18px;width:26px;flex:none;cursor:pointer}
.scope-menu{position:fixed;z-index:1000;min-width:160px;padding:5px;background:var(--bg);border:1px solid var(--bd);border-radius:9px;box-shadow:0 5px 24px #0002}
.scope-menu button{display:block;width:100%;text-align:left;border:0;background:none;color:var(--tx);padding:8px 12px;cursor:pointer}.scope-menu button:hover{background:var(--hover)}

.selection-box{accent-color:var(--accent);width:15px;height:15px;flex:none;margin:0 7px 0 0;cursor:pointer}
#selectionbar{display:flex;gap:6px;align-items:center;padding:0 12px 10px 14px;font-size:12px;line-height:18px;color:var(--tx2)}
#selectionbar .selcount{white-space:nowrap;font-variant-numeric:tabular-nums}
#selectionbar .sp{flex:1}
#selectionbar button{border:0;background:var(--l2);color:var(--tx);padding:4px 9px;border-radius:6px;cursor:pointer;font:inherit;font-size:12px;white-space:nowrap}
#selectionbar button:hover{background:var(--l3)}
#selectionbar button.link{background:var(--accent-bg);color:var(--accent);font-weight:500;box-shadow:inset 0 0 0 .5px var(--accent)}
#selectionbar button.link:hover{background:var(--accent-bg);filter:brightness(.96)}
#selectionbar .selall{display:inline-flex;align-items:center;gap:1px;cursor:pointer;color:var(--tx);user-select:none}
#selectionbar .selall .selection-box{margin:0 5px 0 0}
#selectionbar .selcount{color:var(--tx3);margin-right:4px}
#selectionbar button.x{background:none;color:var(--tx3);padding:4px 6px;font-size:12px;line-height:18px}
#selectionbar button.x:hover{background:var(--hover);color:var(--tx)}
#selectionbar button.pri{background:var(--fill);color:var(--fill-fg)}
#selectionbar button.pri:hover{background:var(--fill-hover)}
#selectionbar button:disabled{opacity:.45;cursor:default}
.selection-mode #selection-start{display:none}

#export{display:none!important}

.selection-mode .side-top{height:258px;flex-basis:258px}
#card .xstat{margin:8px 0;padding-left:18px;color:var(--tx2)}
#card .xstat li{margin:2px 0}
#card .xfail{color:var(--err-text);margin-bottom:0}
#card .xwait{display:flex;align-items:center;gap:8px;color:var(--tx2)}
#card .xsave{display:flex;align-items:center;gap:8px;margin-top:14px;padding:8px 10px;border-radius:8px;background:var(--l2);font-size:12px;line-height:18px;min-width:0}
#card .xsave .k{flex:none;color:var(--tx3)}
#card .xsave .p{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left;color:var(--tx);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px}
#card .xlink{flex:none;border:0;background:none;padding:0;color:var(--accent);font:inherit;font-size:12px;cursor:pointer}
#card .xlink:hover{text-decoration:underline}
#card .xlink:disabled{color:var(--tx3);cursor:default;text-decoration:none}
#card .xsave.xtype{margin-top:6px;background:none;padding:0}
#card .xsave.xtype[hidden]{display:none}
#card .xsave input{flex:1;min-width:0;height:28px;border-radius:6px;border:.5px solid var(--bd3);background:var(--bg);color:var(--tx);padding:0 8px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace;outline:none}
#card .xpath{margin:6px 0 0;padding:8px 10px;border-radius:8px;background:var(--l2);font:11.5px/18px ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere;color:var(--tx)}

#selection-start{margin-left:auto;flex:none;display:inline-flex;align-items:center;gap:6px;border:0;border-radius:8px;height:30px;padding:0 13px 0 11px;background:var(--fill);color:var(--fill-fg);font:inherit;font-size:13px;font-weight:600;cursor:pointer}
#selection-start:hover{background:var(--fill-hover)}
#selection-start svg{width:15px;height:15px;flex:none;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
#selection-start[aria-pressed="true"]{background:var(--accent-bg);color:var(--accent)}
#selectionbar[hidden]{display:none!important}
.selection-mode .scope-more,.selection-mode .act[data-more]{display:none!important}
.selection-box{appearance:none;-webkit-appearance:none;width:16px;height:16px;margin:0 5px;flex:none;border:1.5px solid var(--bd3);border-radius:4px;background:var(--bg);cursor:pointer;position:relative}
.selection-box:checked,.selection-box:indeterminate{background:var(--accent);border-color:var(--accent)}
.selection-box:checked::after{content:'';position:absolute;left:4px;top:1px;width:4px;height:8px;border:solid white;border-width:0 2px 2px 0;transform:rotate(45deg)}
.selection-box:indeterminate::after{content:'';position:absolute;left:3px;right:3px;top:6px;height:2px;background:white}
.selection-box:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.selection-mode .item .acts{opacity:1!important;visibility:visible!important;pointer-events:auto!important;position:static!important;display:flex;flex:none;background:none}
.selection-mode .item .t,.selection-mode .folder .fname{min-width:0;flex:1}
.selection-mode .item .meta{display:none}
</style>
</head>
<body>
<div id="app">
  <aside id="side">
    <div class="side-top">
      <div class="brandrow">
        <a class="logo" id="brand-home" href="#" title="ProveLog"><svg class="cv"
          viewBox="0 0 52.82 101.00" aria-hidden="true"><path
          d="M9.00 9.00 L43.82 50.50 L9.00 92.00"/></svg>provelog</a>
      </div>
      <nav id="agents" class="agents" aria-label="Agents"></nav>
    </div>
    <div class="workspaces-head">
      <span class="wtitle" data-t="workspaces">Workspaces</span>
      <span class="sp"></span>
      <button id="searchbtn" class="ib sm" type="button" data-t-title="search"
              aria-controls="searchpanel" aria-expanded="false">
        <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2L14 14"/></svg>
      </button>
      <button id="sortbtn" class="ib sm" type="button" data-t-title="sortNewest">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M5 8h6M7 12h2"/></svg>
      </button>

    </div>
    <div id="searchpanel" class="searchpanel hidden">
      <input id="q" data-t-ph="search" placeholder="搜索会话" autocomplete="off">
      <button id="searchclose" class="ib sm" type="button" data-t-title="close">×</button>
    </div>
    <div id="list"></div>
    <div id="sidefoot">
      <span class="accounticon" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M3 4h10M3 8h10M3 12h7"/></svg></span>
      <span id="count"></span>
    </div>

  </aside>

  <main id="main">
    <header id="top" class="hidden">
      <div class="h">
        <div id="title"></div>
        <span id="capture" class="capbadge session-only" hidden></span>
        <div id="meta"></div>
        <div id="viewtabs" class="viewtabs" role="tablist">
          <button data-view="chat" role="tab" data-t="viewChat">对话</button>
          <button data-view="trajectory" role="tab" data-t="viewTrajectory">轨迹</button>
        </div>
      </div>
      <div class="acts">
        <button id="parent" class="btn session-only" hidden></button>
        <button id="siblings" class="btn session-only" hidden></button>
        <button id="children" class="btn session-only" hidden></button>
        <button id="stamp" class="pill session-only" type="button" hidden></button>
        <button id="export" class="btn session-only" data-t="export">导出这个会话</button>
        <button id="del" class="ib danger session-only" data-t-title="delete" title="删除会话">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"
               stroke-width="1.2"><path d="M2.5 4h11M6 4V2.8c0-.4.3-.8.8-.8h2.4c.5 0 .8.4.8.8V4M4 4l.6 9c0 .6.5 1 1 1h4.8c.5 0 1-.4 1-1L12 4"/><path d="M6.5 6.5v5M9.5 6.5v5"/></svg>
        </button>
        <button id="info" class="ib session-only" data-t-title="details" title="会话详情">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"
               stroke-width="1.2"><circle cx="8" cy="8" r="6.3"/><path d="M8 7.2v4M8 4.9v.1"/></svg>
        </button>
        <span class="sep"></span>
        <button id="theme" class="flat" type="button"></button>
        <div class="lang-switcher">
          <button id="lang" class="flat" type="button" aria-haspopup="listbox"
                  aria-expanded="false">
            <svg class="globe" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M2.2 8h11.6M8 2c1.9 2.2 1.9 9.8 0 12M8 2c-1.9 2.2-1.9 9.8 0 12"/></svg>
            <span class="curr-lang">简体中文</span>
            <svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 6.2L8 9.6l3.5-3.4"/></svg>
          </button>
          <div id="langmenu" class="lang-dropdown" role="listbox"></div>
        </div>
      </div>
    </header>
    <div id="kidmenu" class="kidmenu hidden"></div>

    <div id="empty"><div class="box"></div></div>
    <div id="trajectory" class="hidden"></div>
    <div id="flow" class="hidden"></div>
    <nav id="outline" aria-label="Outline"></nav>
    <nav id="turnrail" class="hidden" aria-label="Turn navigation">
      <div id="turnmarks"></div>
    </nav>
  </main>

  <aside id="drawer">
    <div class="dh"><span data-t="details">会话详情</span>
      <span id="utc" class="utc" aria-label="UTC time"></span>
      <button class="ib" id="closeDrawer">✕</button></div>
    <div class="dc" id="dcontent"></div>
  </aside>
</div>

<div id="lightbox" class="lb" role="dialog" aria-modal="true" hidden>
  <div class="lbs">
    <div class="lbmask" aria-hidden="true"></div>
    <img class="lbimg" alt="">
    <button class="lbclose" type="button" data-t-title="close" title="关闭" aria-label="关闭">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>
    </button>
  </div>
</div>

<div id="modal"><div id="card"></div></div>
<div id="rowmenu" class="menu" hidden></div>
<div id="toast"></div>

<script>
"use strict";
const BASE = location.pathname.replace(/[^/]*$/, "");
const PAGE_SIZE = 80;
const state = { sessions: [], current: null, filter: null, sort: "newest", q: "", lang: null,
                shown: PAGE_SIZE, platform: "", apps: {}, folderClosed: new Set(),
                folderExpanded: new Set(),
                view: "chat", trajectory: null, trajSid: "", trajOpen: new Set(),
                msgLimit: 300, mdSid: "",
                trajClosed: new Set(), trajLimit: 400, trajQ: "" };

const AGENT_ICONS = __AGENT_ICONS__;
const AGENT_LABELS = {
  dsh: { zh: "DeepSeek Harness", en: "DeepSeek Harness" },
  "doubao": { zh: "豆包", en: "Doubao" },
  "doubao-work": { zh: "豆包工作", en: "Doubao Work" },
  minimax: { zh: "MiniMax Code", en: "MiniMax Code" },
  "qoder-cn": { zh: "Qoder CN", en: "Qoder CN" },
  codebuddy: { zh: "CodeBuddy", en: "CodeBuddy" },
  workbuddy: { zh: "WorkBuddy", en: "WorkBuddy" },
  "kimi-code": { zh: "Kimi Code", en: "Kimi Code" },
  "claude-code": { zh: "Claude Code", en: "Claude Code" },
  codex: { zh: "Codex", en: "Codex" },
  gemini: { zh: "Gemini CLI", en: "Gemini CLI" },
  qwen: { zh: "Qwen Code", en: "Qwen Code" },
  pi: { zh: "pi", en: "pi" },
  omp: { zh: "omp", en: "omp" },
  opencode: { zh: "OpenCode", en: "OpenCode" },
  kilo: { zh: "Kilo", en: "Kilo" },
  cline: { zh: "Cline", en: "Cline" },
  roo: { zh: "Roo Code", en: "Roo Code" },
  openclaw: { zh: "OpenClaw", en: "OpenClaw" },
  hermes: { zh: "Hermes", en: "Hermes" },
  cursor: { zh: "Cursor", en: "Cursor" },
  copilot: { zh: "GitHub Copilot", en: "GitHub Copilot" },
  antigravity: { zh: "Antigravity", en: "Antigravity" },
  zcode: { zh: "ZCode", en: "ZCode" },
  qoder: { zh: "Qoder", en: "Qoder" },
  "codebuddy-code": { zh: "CodeBuddy Code", en: "CodeBuddy Code" },
};
function agentLabel(agent) {
  const row = AGENT_LABELS[agent];
  return row ? (row[state.lang] || row.en) : String(agent || "").replace(/(^|[-_])([a-z])/g,
    (_, a, b) => (a ? " " : "") + b.toUpperCase());
}
const SURFACE_LABELS = {
  cli: { zh: "CLI", en: "CLI" },
  desktop: { zh: "桌面版", en: "Desktop" },
  vscode: { zh: "VS Code", en: "VS Code" },
  app: { zh: "App", en: "App" },
};
function surfaceLabel(surface) {
  const row = SURFACE_LABELS[surface];
  return row ? (row[state.lang] || row.en) : String(surface || "");
}
function tsaLabel(provider) {
  const raw = String(provider || "").trim();
  if (!raw) return "";
  
  
  
  let host = raw;
  try {
    host = new URL(raw).hostname || raw;
  } catch (e) {
    
  }
  const p = host.toLowerCase();
  if (p.includes("sectigo")) return "Sectigo";
  if (p.includes("freetsa")) return "FreeTSA";
  if (p.includes("digicert")) return "DigiCert";
  if (p.includes("certum")) return "Certum";
  if (/^(?:provelog[\s-]+)?test[\s-]?tsa/.test(p)) return "TestTSA";
  return raw;
}
const DARK_GLYPH_LOGOS = new Set(["cline", "copilot", "hermes", "pi"]);
const DARK_TILE_LOGOS = new Set(["cursor", "omp", "roo", "opencode", "zcode", "kilo"]);
function agentIcon(agent) {
  const src = AGENT_ICONS[agent];
  
  
  const kind = DARK_GLYPH_LOGOS.has(agent) ? " glyph" : DARK_TILE_LOGOS.has(agent) ? " tile" : "";
  if (src) return '<img class="agentlogo' + kind + '" src="' + src + '" alt="" aria-hidden="true">';
  return '<span class="agentmono" aria-hidden="true">'
    + esc(agentLabel(agent).slice(0, 1).toUpperCase()) + "</span>";
}

const STR = {
  zh: {
    brand: "provelog", brandSub: "只读 · 本地", brandTitle: "ProveLog 本地记录",
    search: "搜索会话",
    copy: "复制",
    codeBlock: "代码块",
    runningCommands: "执行命令",
    toolActivity: "调用工具",
    workspaces: "工作区",
    sortNewest: "按最近活动排序",
    sortOldest: "按最早活动排序",
    export: "导出这个会话",
    delete: "删除会话", details: "会话详情", all: "全部", stamped: "已盖章",
    untitled: "未命名会话",
    today: "今天", yesterday: "昨天", week: "最近 7 天", earlier: "更早", thinking: "思考",
    turnProcessDone: "已完成，用时 {d}", turnProcessNoTime: "已完成", turnProcess: "过程",
    turnStopped: "已停止", turnFailed: "处理失败",
    processNote: "说明",
    sealOk: "已盖章", sealNo: "未盖章", sealWaiting: "等待回执",
    sealOkN: "已盖章 {n} 轮", sealNoN: "未盖章 {n} 轮", turnNo: "第 {n} 轮", turnsTalk: "{n} 轮对话",
    you: "你", outlineTitle: "大纲", turnNoText: "第 {n} 轮（无文字）",
    boundOkFirst: "以上 {n} 轮已盖章 · {p} 时间戳（最近一次 {t}）",
    boundOkRange: "第 {a}–{b} 轮已盖章 · {p} 时间戳（最近一次 {t}）",
    boundOkOne: "第 {a} 轮已盖章 · {p} 时间戳 {t}",
    boundNo: "以下 {n} 轮未盖章 · {why}",
    boundNoWhy: "已签名入链，还没有时间戳", boundNoWait: "时间戳请求已提交，等待回执",
    evOpenHint: "查看这一轮的盖章明细", evTitle: "第 {n} 轮的盖章明细", evClose: "收起",
    evState: "状态", evSig: "签名",
    evWhyOk: "{p} 在 {t} 签发的时间戳覆盖了这一轮，能证明这一轮的内容在该时刻之前已经存在。",
    evWhyNone: "这一轮已在本机签名并接入哈希链，还没有第三方时间戳。",
    evWhyWait: "这一轮已在本机签名并接入哈希链；时间戳请求已提交，回执还没到。回执到达前不算已盖章。",
    evSigText: "Ed25519，本机密钥。本页不验签，请用 provelog verify。",
    fileListed: "附件只记录了名称、类型和大小，没有保存文件内容。", turns: "轮", turnsOf: "{a} / {b} 轮", turnsN: "{n} 轮", stampOk: "已盖章", stampWait: "等待回执",
    stampNone: "未盖章",
    stampPartial: "部分盖章",
    stampGenTime: "签发时间",
    stampNoneHint: "这段记录没有第三方时间戳（只有本地签名），点开详情",
    captureCaptured: "实时记录", captureIngested: "导入记录", captureUnknown: "未知",
    captureCapturedHint: "ProveLog 在事件发生时就在场。",
    captureIngestedHint: "宿主先写了自己的日志，ProveLog 事后读入；内容一样，但可信上限取决于宿主那份文件有没有被改。",
    captureUnknownHint: "这条旧记录没有采集方式信息。",
    proofSection: "盖章",
    checkpoint: "定格",
    checkpointHint: "记录器每 15 分钟为已有记录写一条签名承诺：这些内容到那一刻为止不再改变。",
    state: "状态",
    location: "位置",
    integrity: "完整性",
    integrityOk: "记录完整",
    integrityBad: "记录不完整",
    integrityPending: "尚未逐条校验",
    integrityState: "哈希链一致",
    integrityBadState: "校验未通过",
    integrityCheck: "逐条重算哈希与链接（本地，无需联网）",
    proofHistory: "盖章记录",
    proofCurrent: "当前",
    proofPending: "等待回执",
    proofStatus: "状态",
    proofNote: "时间戳由独立 TSA（RFC 3161）签发：证明这段记录在某时刻已存在且未被改动，不证明内容由谁生成。", emptyTitle: "还没有任何记录",
    emptyBody: "你使用支持的工具时，记录会自动出现在这里。",
    emptyHint: "安装：<code>pip install provelog</code> 然后 <code>provelog setup</code>",
    filteredTitle: "没有匹配的会话", filteredBody: "换一个关键词，或点“全部”。",
    pickTitle: "选择一个会话", pickBody: "从左边挑一个，阅读完整对话。", basics: "基本信息",
    switchLang: "切换语言（中 / EN）",
    revealFinder: "在访达中显示",
    revealExplorer: "在资源管理器中显示",
    revealFiles: "在文件管理器中显示",
    loadMore: "显示另外 {n} 个会话",
    showMore: "显示另外 {n} 个会话",
    openFail: "打不开这个会话",
    agentHintApp: "单击打开最近会话 · 双击打开 {app}",
    agentHintCli: "单击打开最近会话 · 仅 CLI",
    agentNoApp: "这个工具只有 CLI，没有可打开的客户端",
    agentNoSessions: "这个工具还没有会话",
    allNoSessions: "还没有会话",
    agentOpened: "已打开 {app}",
    agentOpenFail: "打不开这个应用",
    folderHint: "单击折叠/展开 · 双击打开工作区文件夹",
    folderHintNone: "单击折叠/展开 · 这个工作区没有本地文件夹",
    workspaceOpened: "已打开工作区文件夹",
    workspaceOpenFail: "这个工作区没有可打开的本地文件夹",
    folderOpened: "已打开证据文件夹",
    folderOpenFail: "打不开这个文件夹",
    itemHint: "双击打开证据文件夹",
    imageMissing: "图片未保存（引用还在，文件已不在）",
    imageBroken: "图片加载失败",
    fileOpened: "已打开文件",
    fileOpenFail: "打不开这个文件",
    toDark: "切换到深色",
    more: "更多",
    menuExport: "导出",
    menuCopyId: "复制会话 ID",
    menuCopyPath: "复制文件夹路径",
    menuReveal: "在 Finder 中显示",
    menuDelete: "删除会话",
    copiedId: "会话 ID 已复制",
    toLight: "切换到浅色",
    agent: "智能体", started: "开始", last: "最近活动", turns2: "对话轮数",
    models: "模型", stamp: "时间戳", stampAt: "盖章时间", tsa: "时间戳机构",
    coverage: "已盖章覆盖", size: "大小", folder: "文件夹", copyCmd: "复制证据文件夹路径",
    reveal: "在 Finder 中显示", revealDone: "已在 Finder 中打开",
    copyDone: "已复制", copied: "已复制", copyFail: "复制失败，请手动选择",
    delTitle: "删除这个会话？", delBody: "会删除整个文件夹，记录不可恢复。",
    delCancel: "取消", delGo: "删除", deleted: "已删除", delFail: "删除失败",
    exportStart: "正在打包…", exportFail: "导出失败",
    exportNotStampedTitle: "这段对话还没有全部盖章",
    exportNotStampedBody: "共 {n} 轮对话，其中 {m} 轮有第三方时间戳。",
    exportNotStampedBodyPlain: "这段对话还有内容没有第三方时间戳。",
    exportNotStampedHint: "盖章一般在一分钟内完成；完成后再导出，包里才会带时间戳。现在导出，包内会写明实际覆盖范围（未盖章或部分盖章）。",
    exportStampedDone: "全部已盖章 · 导出完成",
    readOnly: "只读预览", fullRecord: "完整记录", openInFinder: "在 Finder 中显示", stampedCover: "已盖章 · 覆盖",
    committed: "已定格", live: "进行中", headOmitted: "安装前开始，开头未记录",
    forkOf: "分叉自 {id}。",
    forkLegacyReadOnly: "旧版嵌套分叉，只读；导出/删除请使用父会话。",
    childParent: "← 父会话",
    childrenCount: "已记录 {n} 个直接子智能体",
    childKindFork: "分叉",
    childKindSubagent: "子智能体",
    childRunning: "正在运行",
    childCompleted: "已完成",
    childInactive: "运行状态未知",
    siblingsCount: "{n} 个同级",
    noProject: "未分组",
    viewChat: "对话", viewTrajectory: "轨迹",
    trajLoading: "正在加载轨迹…", trajEmpty: "这段会话还没有轨迹记录。",
    trajTurn: "Turn {n}", trajStep: "Step {n}", trajTurns: "{n} 轮",
    trajCollapse: "收起轮次", trajExpand: "展开轮次",
    trajUser: "用户", trajAssistant: "助手", trajTool: "工具",
    trajCompaction: "压缩", trajEarlier: "加载更早的轨迹",
    laterMessages: "加载更多 {n} 条消息",
    trajEvent: "事件", trajContent: "内容", trajTokens: "Token", trajTime: "时间",
    trajSearch: "搜索轨迹", trajNoMatch: "没有匹配的轨迹记录。",
    gapBanner: "记录中断：{span} 未记录（用户暂停）。",
    gapTurns: "Turn {a}–{b}", gapSeqs: "host seq {a}–{b}",
    gapOpen: "记录已暂停，尚未恢复。",
    gapUnavailable: "暂停期间的内容无法从宿主日志读取。",
    of: "/", close: "关闭", verifyCmd: "验证命令",
  },
  en: {
    brand: "provelog", brandSub: "read-only · local", brandTitle: "ProveLog — local records",
    search: "Search conversations",
    copy: "Copy",
    codeBlock: "Code block",
    runningCommands: "Running commands",
    toolActivity: "Using tools",
    workspaces: "Workspaces",
    sortNewest: "Newest activity first",
    sortOldest: "Oldest activity first",
    export: "Export this session",
    delete: "Delete conversation", details: "Details", all: "All", stamped: "Timestamped",
    untitled: "Untitled conversation",
    today: "Today", yesterday: "Yesterday", week: "Previous 7 days", earlier: "Earlier", thinking: "Thinking",
    turnProcessDone: "Completed in {d}", turnProcessNoTime: "Completed", turnProcess: "Process",
    turnStopped: "Stopped", turnFailed: "Failed",
    processNote: "Notes",
    sealOk: "Timestamped", sealNo: "Not timestamped", sealWaiting: "awaiting receipt",
    sealOkN: "{n} turns timestamped", sealNoN: "{n} not timestamped", turnNo: "Turn {n}", turnsTalk: "{n} turns",
    you: "YOU", outlineTitle: "Outline", turnNoText: "Turn {n} (no text)",
    boundOkFirst: "The {n} turns above are timestamped · {p} (latest {t})",
    boundOkRange: "Turns {a}–{b} are timestamped · {p} (latest {t})",
    boundOkOne: "Turn {a} is timestamped · {p} · {t}",
    boundNo: "The {n} turns below are not timestamped · {why}",
    boundNoWhy: "signed and chained, no timestamp yet", boundNoWait: "timestamp requested, awaiting the receipt",
    evOpenHint: "Timestamp details for this turn", evTitle: "Turn {n}: timestamp details", evClose: "Close",
    evState: "Status", evSig: "Signature",
    evWhyOk: "A timestamp issued by {p} at {t} covers this turn: it proves the turn's content existed before that moment.",
    evWhyNone: "This turn is signed on this machine and linked into the hash chain. It has no third-party timestamp yet.",
    evWhyWait: "This turn is signed on this machine and linked into the hash chain. A timestamp was requested and the receipt has not arrived; until it does, the turn is not timestamped.",
    evSigText: "Ed25519, this machine's key. This page does not check signatures; use provelog verify.",
    fileListed: "Only the attachment's name, type and size are recorded, not its content.", turns: "turns", turnsOf: "{a} / {b} turns", turnsN: "{n} turns", stampOk: "Timestamped", stampWait: "Timestamping…",
    stampNone: "Not timestamped",
    stampPartial: "Partially timestamped",
    stampGenTime: "Issued at",
    stampNoneHint: "No third-party timestamp yet - open details",
    captureCaptured: "Live capture", captureIngested: "Imported", captureUnknown: "Unknown",
    captureCapturedHint: "ProveLog was present when the event happened.",
    captureIngestedHint: "The host wrote its own log first and ProveLog read it later; the content is the same, but its ceiling depends on that host file.",
    captureUnknownHint: "This older record has no capture-tier information.",
    proofSection: "Proof",
    checkpoint: "Checkpoint",
    checkpointHint: "Every 15 minutes the recorder signs what it already holds: those records are fixed as of that moment.",
    state: "Status",
    location: "Location",
    integrity: "Integrity",
    integrityOk: "Record intact",
    integrityBad: "Record broken",
    integrityPending: "Not checked yet",
    integrityState: "hash chain verified",
    integrityBadState: "verification failed",
    integrityCheck: "re-hashed and re-linked here on this machine, no network",
    proofHistory: "Stamps",
    proofCurrent: "current",
    proofPending: "pending",
    proofStatus: "Status",
    proofNote: "The timestamp is issued by an independent TSA (RFC 3161): it proves this record existed, unchanged, before that moment - not who produced the content.", emptyTitle: "Nothing recorded yet",
    emptyBody: "Records appear here once you use a supported tool with the collector installed.",
    emptyHint: "Install: <code>pip install provelog</code> then <code>provelog setup</code>",
    filteredTitle: "No matching conversations", filteredBody: "Try another word, or pick All.",
    pickTitle: "Pick a conversation", pickBody: "Choose one on the left to read it.", basics: "Basics",
    switchLang: "Switch language (中 / EN)",
    revealFinder: "Show in Finder",
    revealExplorer: "Show in Explorer",
    revealFiles: "Show in file manager",
    loadMore: "Show {n} more sessions",
    showMore: "Show {n} more sessions",
    openFail: "Could not open this conversation",
    agentHintApp: "Click: latest conversation · Double-click: open {app}",
    agentHintCli: "Click: latest conversation · CLI only",
    agentNoApp: "This agent has no desktop app; use its CLI",
    agentNoSessions: "This agent has no conversations yet",
    allNoSessions: "No conversations yet",
    agentOpened: "Opened {app}",
    agentOpenFail: "Could not open the app",
    folderHint: "Click: collapse/expand · Double-click: open workspace folder",
    folderHintNone: "Click: collapse/expand · This workspace has no local folder",
    workspaceOpened: "Opened the workspace folder",
    workspaceOpenFail: "This workspace has no local folder to open",
    folderOpened: "Opened the evidence folder",
    folderOpenFail: "Could not open the folder",
    itemHint: "Double-click: open evidence folder",
    imageMissing: "Image not saved (reference kept, file is gone)",
    imageBroken: "Image failed to load",
    fileOpened: "Opened file",
    fileOpenFail: "Could not open this file",
    toDark: "Switch to dark",
    more: "More",
    menuExport: "Export",
    menuCopyId: "Copy session ID",
    menuCopyPath: "Copy folder path",
    menuReveal: "Show in Finder",
    menuDelete: "Delete conversation",
    copiedId: "Session ID copied",
    toLight: "Switch to light",
    agent: "Agent", started: "Started", last: "Last activity", turns2: "Turns",
    models: "Models", stamp: "Timestamp", stampAt: "Recorded at", tsa: "Timestamp authority",
    coverage: "Stamp coverage", size: "Size", folder: "Folder", copyCmd: "Copy evidence folder path",
    reveal: "Show in Finder", revealDone: "Opened in Finder",
    copyDone: "Copied", copied: "Copied", copyFail: "Could not copy; select it manually",
    delTitle: "Delete this conversation?", delBody: "The whole folder is removed. Records cannot be recovered.",
    delCancel: "Cancel", delGo: "Delete", deleted: "Deleted", delFail: "Delete failed",
    exportStart: "Packing…", exportFail: "Export failed",
    exportNotStampedTitle: "This conversation is not fully timestamped",
    exportNotStampedBody: "{n} turns, {m} with a third-party timestamp.",
    exportNotStampedBodyPlain: "Part of this conversation has no third-party timestamp yet.",
    exportNotStampedHint: "Stamping usually finishes within a minute; export after that if the pack needs the timestamp. Exporting now writes the actual coverage (unstamped or partially stamped) into the pack.",
    exportStampedDone: "All timestamped · exported",
    readOnly: "Read-only preview", fullRecord: "complete record", openInFinder: "Show in Finder", stampedCover: "Timestamped · covers",
    committed: "Committed", live: "Live", headOmitted: "Started before install; the beginning is not recorded",
    forkOf: "Forked from {id}.",
    forkLegacyReadOnly: "Legacy nested fork; read-only. Use the parent conversation for export/delete.",
    childParent: "← Parent",
    childrenCount: "{n} recorded direct subagents",
    childKindFork: "fork",
    childKindSubagent: "subagent",
    childRunning: "running",
    childCompleted: "completed",
    childInactive: "runtime status unknown",
    siblingsCount: "{n} siblings",
    noProject: "Ungrouped",
    viewChat: "Chat", viewTrajectory: "Trajectory",
    trajLoading: "Loading trajectory…", trajEmpty: "No trajectory records yet.",
    trajTurn: "Turn {n}", trajStep: "Step {n}", trajTurns: "{n} turns",
    trajCollapse: "Collapse turns", trajExpand: "Expand turns",
    trajUser: "User", trajAssistant: "Assistant", trajTool: "Tool",
    trajCompaction: "Compaction", trajEarlier: "Load earlier trajectory",
    laterMessages: "Load {n} more messages",
    trajEvent: "Event", trajContent: "Content", trajTokens: "Tokens", trajTime: "Time",
    trajSearch: "Search trajectory", trajNoMatch: "No matching trajectory records.",
    gapBanner: "Recording gap: {span} was not captured (user paused).",
    gapTurns: "Turn {a}–{b}", gapSeqs: "host seq {a}–{b}",
    gapOpen: "Recording is paused and has not resumed.",
    gapUnavailable: "The paused interval could not be read from the host log.",
    of: "of", close: "Close", verifyCmd: "Verify command",
  },
};

function T(key) {
  const pack = STR[state.lang] || STR.zh;
  return pack[key] !== undefined ? pack[key] : (STR.zh[key] ?? key);
}
function tf(key, vars) {
  return T(key).replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ""));
}
function detectLang() {
  try {
    const saved = localStorage.getItem("provelog-lang");
    if (saved === "zh" || saved === "en") return saved;
  } catch (e) {}
  const list = navigator.languages || [navigator.language || ""];
  return list.some((l) => String(l).toLowerCase().startsWith("zh")) ? "zh" : "en";
}


const THEME_KEY = "provelog-theme";
const THEME_ICONS = {
  light: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="3.1"/>'
    + '<path d="M8 1.3v1.9M8 12.8v1.9M1.3 8h1.9M12.8 8h1.9M3.2 3.2l1.4 1.4'
    + 'M11.4 11.4l1.4 1.4M12.8 3.2l-1.4 1.4M4.6 11.4l-1.4 1.4"/></svg>',
  dark: '<svg viewBox="0 0 16 16"><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8'
    + 'a5.6 5.6 0 1 0 6.8 6.8z"/></svg>',
};

function themePref() {
  try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
}
function systemTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark" : "light";
}
function resolvedTheme() {
  return themePref() || systemTheme();
}
function applyTheme() {
  const theme = resolvedTheme();
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById("theme");
  if (!btn) return;
  
  btn.innerHTML = theme === "dark" ? THEME_ICONS.dark : THEME_ICONS.light;
  btn.title = T(theme === "dark" ? "toLight" : "toDark");
}

function toggleTheme() {
  try { localStorage.setItem(THEME_KEY, resolvedTheme() === "dark" ? "light" : "dark"); }
  catch (e) {}
  applyTheme();
}


function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function mdImageUrl(path) {
  const clean = String(path || "");
  if (!state.mdSid || !clean || /^[a-z]+:/i.test(clean)) return "";
  
  
  if (!/[.](png|jpe?g|webp|gif|avif|bmp)([?#].*)?$/i.test(clean)
      && !clean.startsWith("/") && !clean.startsWith("./")
      && !clean.startsWith("../")) return "";
  return BASE + "api/records/session/" + encodeURIComponent(state.mdSid)
    + "/file?path=" + encodeURIComponent(clean);
}

function md(src, opts) {
  const images = !opts || opts.images !== false;
  const s = esc(src ?? "");
  const out = [];
  const lines = s.split("\n");
  let i = 0, list = null;
  const closeList = () => { if (list) { out.push("</" + list + ">"); list = null; } };
  const inline = (t) => {
    
    
    const code = [];
    t = t.replace(/`([^`\n]+)`/g, (_, value) => {
      code.push(value);
      return "\u0000" + (code.length - 1) + "\u0000";
    });
    if (images) {
      t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)/g, (whole, alt, path) => {
        const clean = path.replace(/^<|>$/g, "").replace(/&amp;/g, "&");
        const src = mdImageUrl(clean);
        if (!src) return whole;
        return '<a class="shotlink mdimg" href="' + src + '" target="_blank" rel="noreferrer">'
          + '<img class="shot mdimg" loading="lazy" src="' + src + '" alt="' + alt
        + '" data-fallback="' + esc(T("imageBroken")) + '"></a>';
      });
    }
    t = t
      .replace(/(^|[^!])\[([^\]]+)\]\(([^)\s]+)\)/g, (whole, prefix, label, path) => {
        const clean = path.replace(/^<|>$/g, "").replace(/&amp;/g, "&");
        if (isLocalPath(clean)) {
          return prefix + '<button type="button" class="filelink" data-file="' + esc(clean)
            + '"><svg class="fi" viewBox="0 0 16 16" aria-hidden="true">'
            + '<path d="M4 2.2h5.2l2.8 2.8v8.8H4z"/><path d="M9.2 2.2V5h2.8"/></svg>'
            + label + '</button>';
        }
        const href = safeHref(clean);
        if (!href) return whole;
        return prefix + '<a href="' + esc(href) + '" target="_blank" rel="noreferrer">'
          + label + '</a>';
      })
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    return t.replace(/\u0000(\d+)\u0000/g, (_, index) => "<code>" + code[Number(index)] + "</code>");
  };
  while (i < lines.length) {
    const L = lines[i];
    if (/^\s*```/.test(L)) {
      closeList(); const buf = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push('<div class="codeblock"><div class="codehead"><span>'
        + T("codeBlock") + '</span><span class="sp"></span>'
        + copyBtn(buf.join("\n")) + '</div><pre><code>'
        + buf.join("\n") + "</code></pre></div>");
      continue;
    }
    let m;
    if (/^\s*\|.*\|\s*$/.test(L) && i + 1 < lines.length
        && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])
        && lines[i + 1].includes("-")) {
      closeList();
      const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "")
        .split("|").map((c) => c.trim());
      const head = cells(L);
      i += 2;
      const body = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        body.push(cells(lines[i]));
        i++;
      }
      out.push("<table><thead><tr>"
        + head.map((c) => "<th>" + inline(c) + "</th>").join("")
        + "</tr></thead><tbody>"
        + body.map((r) => "<tr>" + r.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("")
        + "</tbody></table>");
      continue;
    }
    if ((m = L.match(/^(#{1,4})\s+(.*)/))) {
      closeList(); const n = m[1].length;
      out.push("<h" + n + ">" + inline(m[2]) + "</h" + n + ">");
    } else if (/^\s*(---+|\*\*\*+)\s*$/.test(L)) {
      closeList(); out.push("<hr>");
    } else if ((m = L.match(/^\s*>\s?(.*)/))) {
      closeList(); const buf = [m[1]];
      while (i + 1 < lines.length && (m = lines[i + 1].match(/^\s*>\s?(.*)/))) { buf.push(m[1]); i++; }
      out.push("<blockquote><p>" + buf.map(inline).join("<br>") + "</p></blockquote>");
    } else if ((m = L.match(/^\s*[-*]\s+(.*)/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push("<li>" + inline(m[1]) + "</li>");
    } else if ((m = L.match(/^\s*\d+[.)]\s+(.*)/))) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push("<li>" + inline(m[1]) + "</li>");
    } else if (/^\s*$/.test(L)) {
      closeList();
    } else {
      closeList(); const buf = [L];
      while (i + 1 < lines.length && !/^\s*$/.test(lines[i + 1])
             && !/^\s*(```|#{1,4}\s|>|[-*]\s|\d+[.)]\s|---)/.test(lines[i + 1])) {
        i++; buf.push(lines[i]);
      }
      out.push("<p>" + buf.map(inline).join("<br>") + "</p>");
    }
    i++;
  }
  closeList();
  return out.join("");
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return String(d.getHours()).padStart(2, "0") + ":"
    + String(d.getMinutes()).padStart(2, "0");
}
function fmtRel(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  const zh = state.lang === "zh";
  if (sec < 60) return zh ? "刚刚" : "now";
  if (sec < 3600) {
    const n = Math.floor(sec / 60);
    return zh ? n + "分钟" : n + "min";
  }
  if (sec < 86400) {
    const n = Math.floor(sec / 3600);
    return zh ? n + "小时" : n + "h";
  }
  const n = Math.floor(sec / 86400);
  return zh ? n + "天" : n + "d";
}
function fmtWhen(iso) {
  
  
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const now = new Date();
  if (d.getFullYear() === now.getFullYear()
      && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) {
    return fmtTime(iso);
  }
  return fmtDay(iso);
}
function fmtDay(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const zh = state.lang === "zh";
  return zh ? (d.getMonth() + 1) + "-" + String(d.getDate()).padStart(2, "0")
            : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso).slice(0, 16).replace("T", " ");
  const zh = state.lang === "zh";
  const day = zh ? (d.getMonth() + 1) + "-" + String(d.getDate()).padStart(2, "0")
                 : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return day + " " + String(d.getHours()).padStart(2, "0") + ":"
    + String(d.getMinutes()).padStart(2, "0");
}
function groupOf(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "earlier";
  const now = new Date();
  const day = 86400000;
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  if (t >= start) return "today";
  if (t >= start - day) return "yesterday";
  if (t >= start - 7 * day) return "week";
  return "earlier";
}
function fmtTokens(n) {
  if (typeof n !== "number") return "";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
function fmtDuration(seconds) {
  const n = Math.max(0, Math.round(Number(seconds) || 0));
  if (n < 60) return n + "s";
  const m = Math.floor(n / 60);
  if (m < 60) return m + "m " + (n % 60) + "s";
  return Math.floor(m / 60) + "h " + (m % 60) + "m";
}
function projectLabel(project) {
  const raw = String(project || "").replace(/[\\/]+$/, "");
  const leaf = raw.split(/[\\/]/).filter(Boolean).pop();
  if (!leaf) return T("noProject");
  if (leaf === "default-workspace") return "Default workspace";
  return leaf.replace(/[-_]+/g, " ");
}
function safeHref(value) {
  const s = String(value || "").trim();
  if (!s) return "";
  
  
  
  if (/^(https?:|mailto:|tel:)/i.test(s)) return s;
  if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return "";
  if (s.startsWith("//")) return "";
  return s;
}
function isLocalPath(value) {
  const s = String(value || "").trim();
  if (!s || s.startsWith("#")) return false;
  if (/^[a-z][a-z0-9+.-]*:/i.test(s) || s.startsWith("//")) return false;
  return s.startsWith("/") || /^[A-Za-z]:[\\/]/.test(s)
    || s.startsWith("./") || s.startsWith("../")
    || /\.[A-Za-z0-9]{1,8}$/.test(s);
}
function sessionTitle(s) {
  return (s && (s.title || s.title_hint)) || T("untitled");
}


async function api(path, opts) {
  const res = await fetch(BASE + path, Object.assign({
    headers: { "accept-language": state.lang === "zh" ? "zh-CN" : "en-US" },
  }, opts || {}));
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch (e) {}
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res;
}
const apiJson = async (p, o) => (await api(p, o)).json();

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 2200);
}


function toggleSearch(open) {
  const panel = document.getElementById("searchpanel");
  const btn = document.getElementById("searchbtn");
  const q = document.getElementById("q");
  if (!panel || !btn || !q) return;
  const isOpen = open === undefined ? panel.classList.contains("hidden") : open;
  panel.classList.toggle("hidden", !isOpen);
  btn.classList.toggle("on", isOpen);
  btn.setAttribute("aria-expanded", String(isOpen));
  if (isOpen) {
    setTimeout(() => { q.focus(); q.select(); }, 0);
  } else {
    q.value = "";
    state.q = "";
    renderList();
  }
}

function agentsInList() {
  
  
  const latest = new Map();
  for (const s of state.sessions) {
    const at = String(s.last_at || s.started_at || "");
    if (!latest.has(s.agent) || at > latest.get(s.agent)) latest.set(s.agent, at);
  }
  return [...latest.entries()]
    .sort((a, b) => b[1].localeCompare(a[1]))
    .map(([agent]) => agent);
}
let agentClickTimer = null;
let folderClickTimer = null;

function latestSessionForAgent(agent) {
  const rows = state.sessions.filter((s) => !agent || s.agent === agent);
  rows.sort((a, b) => String(b.last_at || b.started_at || "")
    .localeCompare(String(a.last_at || a.started_at || "")));
  return rows[0] || null;
}

function selectAgent(agent) {
  const all = !agent;
  state.filter = all ? null : "agent:" + agent;
  if (state.q) {
    state.q = "";
    const q = document.getElementById("q");
    if (q) q.value = "";
    toggleSearch(false);
  }
  const latest = latestSessionForAgent(agent);
  if (latest) state.folderClosed.delete(latest.project || latest.agent || "");
  renderAgents(); renderList();
  if (!latest) { toast(T(all ? "allNoSessions" : "agentNoSessions")); return; }
  const id = latest.session_id;
  if (decodeURIComponent(location.hash.slice(1)) === id) openSession(id);
  else location.hash = "#" + encodeURIComponent(id);
}

async function openAgentApp(agent) {
  if (!agent) { selectAgent(""); return; }
  if (!state.apps[agent]) { toast(T("agentNoApp")); return; }
  try {
    const res = await apiJson("api/records/agent/" + encodeURIComponent(agent) + "/open",
                              { method: "POST" });
    toast(fill(T("agentOpened"), { app: res.app || state.apps[agent] }));
  } catch (e) {
    toast(e.message || T("agentOpenFail"));
  }
}

let scopeMenu = null;
function closeScopeMenu(){if(scopeMenu) scopeMenu.remove();scopeMenu=null;}
function addScopeButton(parent,kind,value){
 const b=document.createElement('button');b.type='button';b.className='scope-more';b.textContent='⋯';
 b.setAttribute('aria-label',state.lang==='zh'?'更多操作':'More actions');
 b.onkeydown=e=>e.stopPropagation();b.ondblclick=e=>e.stopPropagation();
 b.onclick=e=>{
  e.stopPropagation();clearTimeout(agentClickTimer);clearTimeout(folderClickTimer);closeScopeMenu();
  const menu=document.createElement('div');menu.className='scope-menu';
  const action=(label,run)=>{const x=document.createElement('button');x.textContent=label;x.onclick=()=>{closeScopeMenu();run();};menu.append(x);};
  action(state.lang==='zh'?'复制路径':'Copy path',async()=>{
   try{let path=value;
    if(kind==='agent'||!isLocalPath(value)){
     const data=await apiJson('api/records/sessions');
     const row=(data.sessions||[]).find(r=>r.agent===value&&r.folder);
     if(!row){toast(state.lang==='zh'?'没有可复制的路径':'No path available');return;}
     path=row.folder.slice(0,Math.max(row.folder.lastIndexOf('/'),row.folder.lastIndexOf(String.fromCharCode(92))));
    }await copyText(path);
   }catch(err){toast(err.message||T('copyFail'));}
  });
  action(state.lang==='zh'?(kind==='agent'?'导出此 Agent 的所有对话':'导出此目录的所有对话'):(kind==='agent'?'Export all conversations for this agent':'Export all conversations in this folder'),()=>exportSelection(selectionRows(kind,value).map(r=>r.session_id)));

  document.body.append(menu);scopeMenu=menu;const rect=b.getBoundingClientRect();
  menu.style.left=Math.max(8,Math.min(rect.left,innerWidth-menu.offsetWidth-8))+'px';
  menu.style.top=Math.max(8,Math.min(rect.bottom+4,innerHeight-menu.offsetHeight-8))+'px';
 };parent.append(b);
}
document.addEventListener('click',e=>{if(scopeMenu&&!scopeMenu.contains(e.target)&&!e.target.closest('.scope-more'))closeScopeMenu();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeScopeMenu();});
let selecting=false;const selectedSessions=new Set();
function selectionRows(kind,value){return state.sessions.filter(r=>kind==='agent'?r.agent===value:(r.project||r.agent||'')===value);}
function drawSelection(){
 document.querySelectorAll('.selection-box').forEach(x=>x.remove());
 const zh=state.lang==='zh';
 const brand=document.querySelector('.brandrow');
 
 
 let bar=document.getElementById('selectionbar');
 if(!bar){bar=document.createElement('div');bar.id='selectionbar';}
 if(bar.previousElementSibling!==brand)brand.after(bar);
 document.body.classList.toggle('selection-mode',selecting);
 closeScopeMenu();closeRowMenu();
 let start=document.getElementById('selection-start');
 if(!start){start=document.createElement('button');start.id='selection-start';brand.append(start);}
 start.innerHTML='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.2v7.6M4.9 6.9L8 10l3.1-3.1M2.8 11.4v1.5c0 .5.4.9.9.9h8.6c.5 0 .9-.4.9-.9v-1.5"/></svg><span>'+(zh?'导出…':'Export…')+'</span>';start.setAttribute('aria-pressed',String(selecting));
 start.title=zh?'勾选要导出的会话，可以选一个或多个':'Tick the conversations to export, one or several';
 
 
 start.onclick=()=>{selecting=true;selectedSessions.clear();
  const cur=state.current&&state.current.session_id;
  if(cur&&state.sessions.some(r=>r.session_id===cur))selectedSessions.add(cur);
  drawSelection();
  
  const row=cur&&[...document.querySelectorAll('.item[data-id]')].find(el=>el.dataset.id===cur);
  if(row)row.scrollIntoView({block:'nearest'});};
 bar.hidden=!selecting;
 if(!selecting){bar.innerHTML='';return;}
 const total=state.sessions.length,all=total>0&&selectedSessions.size>=total;
 
 
 
 bar.innerHTML='<label class="selall"><input type="checkbox" class="selection-box" id="selection-all">'+(zh?'全选':'All')+'</label>'
  +'<span class="selcount">'+(zh?'已选 ':'Selected ')+selectedSessions.size+'</span>'
  +'<button id="selection-export" class="pri">'+(zh?'导出':'Export')+'</button>'
  +'<button id="selection-cancel" class="x" title="'+(zh?'退出批量导出':'Leave multi-export')+'" aria-label="'+(zh?'退出批量导出':'Leave multi-export')+'">✕</button>';
 const master=bar.querySelector('#selection-all');
 master.checked=all;
 master.onclick=()=>{if(all)selectedSessions.clear();else state.sessions.forEach(r=>selectedSessions.add(r.session_id));drawSelection();};
 bar.querySelector('#selection-cancel').onclick=()=>{selecting=false;selectedSessions.clear();drawSelection();};
 bar.querySelector('#selection-export').onclick=()=>exportSelection([...selectedSessions]);
 bar.querySelector('#selection-export').disabled=!selectedSessions.size;
 const add=(el,rows)=>{if(!rows.length)return;const box=document.createElement('input');box.type='checkbox';box.className='selection-box';box.setAttribute('aria-label',zh?'选择会话':'Select conversations');
 const n=rows.filter(r=>selectedSessions.has(r.session_id)).length;box.checked=n===rows.length;
 box.onclick=e=>{e.stopPropagation();rows.forEach(r=>box.checked?selectedSessions.add(r.session_id):selectedSessions.delete(r.session_id));drawSelection();};
 box.ondblclick=e=>e.stopPropagation();box.onkeydown=e=>e.stopPropagation();const seat=el.matches('.item')?el.querySelector('.acts'):el;seat.append(box);};
 document.querySelectorAll('.agent-line').forEach(el=>add(el,selectionRows('agent',el.querySelector('.agent').dataset.agent)));
 document.querySelectorAll('.folder').forEach(el=>add(el,selectionRows('group',el.dataset.folder||'')));
 document.querySelectorAll('.item[data-id]').forEach(el=>add(el,state.sessions.filter(r=>r.session_id===el.dataset.id)));
}




let exportRun=0;
async function exportSelection(ids){
 if(!ids.length)return;
 const zh=state.lang==='zh';
 const run=++exportRun;
 const live=()=>run===exportRun&&document.getElementById('modal').classList.contains('open');
 const pause=(ms)=>new Promise(r=>setTimeout(r,ms));
 const post=async(body)=>{const res=await fetch(BASE+'api/records/export-selection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!res.ok){let err={};try{err=await res.json();}catch(e){}throw Error(err.detail||(zh?'导出失败':'Export failed'));}return res;};
 const check=async()=>(await post({ids,preview:true})).json();
 const tilde=(path)=>{const h=state.home||'';return h&&path.indexOf(h)===0?'~'+path.slice(h.length):path;};
 let dirInfo={dir:'',is_default:true};
 
 
 const download=async(list)=>{
  try{
   const out=await (await post({ids:list,allow_unstamped:true,save:true})).json();
   if(selecting){selecting=false;selectedSessions.clear();drawSelection();}
   const dlg=modal('<h3>'+esc(zh?'导出完成':'Export complete')+'</h3>'
    +'<p>'+esc(zh?'已导出 '+out.sessions+' 个会话：':'Exported '+out.sessions+' conversation'+(out.sessions===1?'':'s')+':')+'</p>'
    +'<p class="xpath" title="'+esc(out.path)+'">'+esc(tilde(out.path))+'</p>'
    +'<div class="row-actions"><button class="btn" id="xreveal">'+esc(revealLabel())+'</button>'
    +'<button class="btn pri" data-close>'+esc(zh?'完成':'Done')+'</button></div>',
    (card)=>{card.querySelector('#xreveal').onclick=async()=>{
      try{await fetch(BASE+'api/records/export-reveal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:out.path})});}
      catch(e){toast(e.message);}};});
  }catch(e){toast(e.message);}
 };
 const n=(text,k)=>text.replace('{n}',k);
 let waiting=false;
 const render=(st)=>{
  const bad=new Set((st.failures||[]).map(f=>f.session_id));
  const good=ids.filter(id=>!bad.has(id));
  const c=st.states||{};
  const li=(k,z,e)=>k?'<li>'+esc(n(zh?z:e,k))+'</li>':'';
  let html='<h3>'+esc(zh?'导出 '+st.sessions+' 个会话':'Export '+st.sessions+' conversation'+(st.sessions===1?'':'s'))+'</h3>';
  if(good.length){
   html+='<ul class="xstat">'+li(c.complete,'{n} 个已全部盖章','{n} fully timestamped')
    +li(c.partial,'{n} 个部分盖章','{n} partially timestamped')
    +li(c.none,'{n} 个未盖章','{n} not timestamped')
    +li(c.unsupported,'{n} 个带有本验证器不支持的时间戳机构','{n} with a timestamp authority this verifier does not support')+'</ul>';
   if(!st.complete)html+='<p>'+esc(zh?'没有盖章的部分照样可以导出，但导出包只能证明这些内容被保存下来，不能证明它们在何时已经存在。':'Parts without a timestamp can still be exported, but the pack can only show they were saved, not when they already existed.')+'</p>';
   if(!st.complete&&st.waiting==='blocked')html+='<p>'+esc(zh?'目前取不到时间戳（上一次请求失败，或机构不受支持），等待不会改变结果。':'A timestamp cannot be obtained right now (the last request failed, or the authority is unsupported); waiting will not change this.')+'</p>';
   if(st.missing_attachments)html+='<p>'+esc(n(zh?'有 {n} 个附件文件已不在磁盘上，不会放进导出包；对话记录本身照常导出。':'{n} attachment file(s) are no longer on disk and will not be included; the conversation itself is still exported.',st.missing_attachments))+'</p>';
  }
  if(bad.size){
   html+='<p class="xfail">'+esc(n(zh?'{n} 个会话的记录没有通过校验，不会被导出：':'{n} conversation(s) failed verification and will not be exported:',bad.size))+'</p><ul class="xstat">'
    +st.failures.slice(0,5).map(f=>'<li>'+esc((f.title||f.session_id)+' — '+f.reason)+'</li>').join('')
    +(bad.size>5?'<li>…</li>':'')+'</ul>';
  }
  if(waiting)html+='<p class="xwait"><span class="tspin"></span><span>'+esc(zh?'正在等待时间戳回执，最多 90 秒…':'Waiting for timestamp receipts, up to 90 seconds…')+'</span></p>';
  if(good.length&&dirInfo.dir)html+='<div class="xsave"><span class="k">'+esc(zh?'保存到':'Save to')+'</span>'
   +'<span class="p" title="'+esc(dirInfo.dir)+'"><bdi dir="ltr">'+esc(tilde(dirInfo.dir))+'</bdi></span>'
   +'<button type="button" class="xlink" id="xdir">'+esc(zh?'更改…':'Change…')+'</button>'
   +(dirInfo.is_default?'':'<button type="button" class="xlink" id="xdirreset">'+esc(zh?'恢复默认':'Reset')+'</button>')+'</div>'
   +'<div class="xsave xtype" hidden><input id="xdirinput" spellcheck="false" autocomplete="off" placeholder="'+esc(zh?'输入文件夹的完整路径':'Full path of a folder')+'">'
   +'<button type="button" class="btn" id="xdirok">'+esc(zh?'确定':'OK')+'</button></div>';
  const canWait=!waiting&&good.length&&!st.complete&&st.waiting!=='blocked';
  const goLabel=bad.size?n(zh?'导出其余 {n} 个':'Export the other {n}',good.length):st.complete?(zh?'导出':'Export'):(zh?'坚持导出':'Export anyway');
  html+='<div class="row-actions"><button class="btn" data-close>'+esc(waiting?(zh?'停止等待':'Stop waiting'):(zh?'取消':'Cancel'))+'</button>'
   +(canWait?'<button class="btn" id="xwait">'+esc(zh?'等待':'Wait')+'</button>':'')
   +(good.length&&!waiting?'<button class="btn pri" id="xgo">'+esc(goLabel)+'</button>':'')+'</div>';
  const dlg=modal(html,(card)=>{
   const go=card.querySelector('#xgo');if(go)go.onclick=()=>{exportRun++;dlg.close();download(good);};
   const w=card.querySelector('#xwait');if(w)w.onclick=()=>wait(st);
   const setDir=async(path,body)=>{
    const res=await fetch(BASE+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
    const data=await res.json().catch(()=>({}));
    if(!res.ok)throw Error(data.detail||(zh?'无法使用这个文件夹':'That folder cannot be used'));
    return data;};
   const d=card.querySelector('#xdir');
   if(d)d.onclick=async()=>{
    
    
    d.disabled=true;const label=d.textContent;d.textContent=zh?'请在弹出的窗口里选择…':'Choose in the window that opened…';
    try{const data=await setDir('api/records/export-dir/choose');
     if(!live())return;
     if(data.supported===false){card.querySelector('.xtype').hidden=false;card.querySelector('#xdirinput').focus();}
     else if(data.dir){dirInfo=data;render(st);return;}
    }catch(e){toast(e.message);}
    d.disabled=false;d.textContent=label;};
   const r=card.querySelector('#xdirreset');
   if(r)r.onclick=async()=>{try{dirInfo=await setDir('api/records/export-dir',{dir:''});if(live())render(st);}catch(e){toast(e.message);}};
   const ok=card.querySelector('#xdirok');
   if(ok)ok.onclick=async()=>{try{dirInfo=await setDir('api/records/export-dir',{dir:card.querySelector('#xdirinput').value});if(live())render(st);}catch(e){toast(e.message);}};
  });
 };
 const wait=async(st)=>{
  waiting=true;render(st);
  const end=Date.now()+90000;
  let cur=st;
  while(Date.now()<end){
   await pause(2000);
   if(!live())return;                       
   try{cur=await check();}catch(e){toast(e.message);break;}
   if(!live())return;
   if(cur.complete||cur.waiting==='blocked')break;
   render(cur);
  }
  waiting=false;if(live())render(cur);
 };
 modal('<h3>'+esc(zh?'导出':'Export')+'</h3><p class="xwait"><span class="tspin"></span><span>'+esc(zh?'正在检查记录、附件和时间戳…':'Checking records, attachments and timestamps…')+'</span></p><div class="row-actions"><button class="btn" data-close>'+esc(zh?'取消':'Cancel')+'</button></div>');
 let st;
 try{[st,dirInfo]=await Promise.all([check(),apiJson('api/records/export-dir')]);}
 catch(e){if(live())document.getElementById('modal').classList.remove('open');toast(e.message);return;}
 if(!live())return;
 render(st);
}
function renderAgents() {
  const host = document.getElementById("agents");
  const counts = {};
  for (const s of state.sessions) counts[s.agent] = (counts[s.agent] || 0) + 1;
  const items = [{ id: null, label: T("all"), count: state.sessions.length }];
  for (const a of agentsInList()) {
    items.push({ id: "agent:" + a, label: agentLabel(a), agent: a, count: counts[a] || 0 });
  }
  host.innerHTML = items.map((c) => {
    const on = (state.filter || null) === c.id ? " on" : "";
    const app = c.agent ? state.apps[c.agent] : "";
    const hint = c.agent
      ? (app ? fill(T("agentHintApp"), { app: app }) : T("agentHintCli"))
      : "";
    return '<button class="agent' + on + '" data-f="' + esc(c.id ?? "")
      + '" data-agent="' + esc(c.agent || "") + '" title="' + esc(hint) + '">'
      + (c.agent ? agentIcon(c.agent) : '<span class="adot"></span>')
      + '<span class="an">' + esc(c.label) + "</span>"
      + '<span class="ac">' + c.count + "</span></button>";
  }).join("");
  for (const el of host.querySelectorAll('.agent[data-agent]')) {
    if (!el.dataset.agent) continue;
    const line = document.createElement('div'); line.className = 'agent-line';
    el.before(line); line.append(el); addScopeButton(line, 'agent', el.dataset.agent);
  }
  for (const el of host.querySelectorAll(".agent")) {
    
    
    
    el.onclick = (e) => {
      if (e.detail > 1) { clearTimeout(agentClickTimer); return; }
      clearTimeout(agentClickTimer);
      agentClickTimer = setTimeout(() => selectAgent(el.dataset.agent || ""), 220);
    };
    el.ondblclick = (e) => {
      e.preventDefault();
      clearTimeout(agentClickTimer);
      openAgentApp(el.dataset.agent || "");
    };
  }
}

function visibleSessions() {
  const q = state.q.trim().toLowerCase();
  const rows = state.sessions.filter((s) => {
    if (state.filter && state.filter.startsWith("agent:")
        && s.agent !== state.filter.slice(6)) return false;
    if (q) {
      const hay = (sessionTitle(s) + " " + (s.title_hint || "") + " "
        + agentLabel(s.agent) + " " + (s.surface ? surfaceLabel(s.surface) : "")
        + " " + s.agent + " " + (s.session_id || "")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const order = (s) => String(s.last_at || s.started_at || "");
  if (state.sort === "oldest") rows.sort((a, b) => order(a).localeCompare(order(b)));
  else rows.sort((a, b) => order(b).localeCompare(order(a)));
  return rows;
}

async function openWorkspace(sessionId) {
  if (!sessionId) { toast(T("workspaceOpenFail")); return; }
  try {
    await api("api/records/session/" + encodeURIComponent(sessionId) + "/open-workspace",
              { method: "POST" });
    toast(T("workspaceOpened"));
  } catch (e) { toast(e.message || T("workspaceOpenFail")); }
}

async function openSessionFolder(sessionId) {
  if (!sessionId) return;
  try {
    await api("api/records/session/" + encodeURIComponent(sessionId) + "/open-folder",
              { method: "POST" });
    toast(T("folderOpened"));
  } catch (e) { toast(e.message || T("folderOpenFail")); }
}


const ROW_IC = {
  more: '<svg viewBox="0 0 24 24" aria-hidden="true">'
    + '<circle class="dot" cx="5.5" cy="12" r="1.9"/><circle class="dot" cx="12" cy="12" r="1.9"/>'
    + '<circle class="dot" cx="18.5" cy="12" r="1.9"/></svg>',
};

function renderList(reset) {
  const rows = visibleSessions();
  const list = document.getElementById("list");
  if (!rows.length) { list.innerHTML = ""; return; }
  if (reset !== false) state.shown = PAGE_SIZE;
  
  
  const slice = rows.slice(0, state.shown);
  const folders = new Map();
  for (const s of slice) {
    const key = s.project || s.agent || "";
    if (!folders.has(key)) folders.set(key, []);
    folders.get(key).push(s);
  }
  function workspaceRows(group) {
    
    
    
    const byAgent = new Map();
    for (const row of group) {
      if (!byAgent.has(row.agent)) byAgent.set(row.agent, []);
      byAgent.get(row.agent).push(row);
    }
    const at = (row) => String(row.last_at || row.started_at || "");
    const latest = (rows) => rows.reduce((m, row) => at(row) > m ? at(row) : m, "");
    const agents = [...byAgent.entries()].sort((a, b) => {
      return latest(b[1]).localeCompare(latest(a[1]));
    });
    const out = [];
    for (const [, rows] of agents) {
      rows.sort((a, b) => {
        const ao = at(a), bo = at(b);
        return state.sort === "oldest" ? ao.localeCompare(bo) : bo.localeCompare(ao);
      });
      out.push(...rows);
    }
    return out;
  }

  let html = "";
  for (const [key, group] of folders) {
    const ordered = workspaceRows(group);
    const closed = state.folderClosed.has(key);
    const label = key ? projectLabel(key) : T("noProject");
    const on = state.current
      && (state.current.project || state.current.agent || "") === key;
    const workspaceSession = ordered.length ? ordered[0].session_id : "";
    const canOpen = isLocalPath(key);
    html += '<div class="folder' + (closed ? " closed" : "") + (on ? " on" : "")
      + '" data-folder="'
      + esc(key) + '" data-workspace="' + esc(workspaceSession)
      + '" title="' + esc(canOpen ? T("folderHint") : T("folderHintNone"))
      + '" role="button" tabindex="0">'
      + '<svg class="fico" viewBox="0 0 16 16" aria-hidden="true"><path d="M1.8 4.2h4l1.3 1.5h7.1v7.5H1.8z"/></svg>'
      + '<span class="fname">' + esc(label) + "</span></div>";
    if (closed) continue;
    const expanded = state.folderExpanded.has(key);
    let shownGroup = expanded ? ordered : ordered.slice(0, 4);
    const currentRow = ordered.find((row) => state.current
      && state.current.session_id === row.session_id);
    if (currentRow && !shownGroup.includes(currentRow)) shownGroup = shownGroup.concat([currentRow]);
    for (const s of shownGroup) {
      const cur = state.current && state.current.session_id === s.session_id ? " on" : "";
      const when = fmtWhen(s.last_at || s.started_at);
      html += '<div class="item' + cur + '" data-id="'
        + esc(s.session_id)
        + '" tabindex="0" role="button" title="' + esc(agentLabel(s.agent)
        + (s.surface ? " · " + surfaceLabel(s.surface) : "")
        + (s.turns ? " · " + s.turns + " " + T("turns") : "")
        + " · " + fmtDateTime(s.last_at || s.started_at)
        + " · " + T("itemHint")) + '">'
        + '<span class="rowagent" title="' + esc(agentLabel(s.agent)) + '">'
        + agentIcon(s.agent) + "</span>"
        + '<div class="t">' + esc(sessionTitle(s)) + "</div>"
        + '<span class="meta"><span class="time">' + esc(when) + "</span>"
        + "</span>"
        + '<span class="acts">'
        + '<button type="button" class="act" data-more="' + esc(s.session_id)
          + '" title="' + esc(T("more")) + '" aria-label="' + esc(T("more"))
          + '">' + ROW_IC.more + "</button>"
        + "</span></div>";
    }
    if (ordered.length > shownGroup.length) {
      html += '<button class="more-row folder-more" data-folder-more="'
        + esc(key) + '">'
        + esc(fill(T("showMore"), { n: ordered.length - shownGroup.length })) + "</button>";
    }
  }
  if (rows.length > slice.length) {
    html += '<button class="more-row" id="loadmore">'
      + esc(T("loadMore").replace("{n}", rows.length - slice.length)) + "</button>";
  }
  const top = list.scrollTop;
  list.innerHTML = html;
  if (reset === false) list.scrollTop = top;
  for (const el of list.querySelectorAll(".folder")) {
    addScopeButton(el, "group", el.dataset.folder || "");
    const toggle = () => {
      const key = el.dataset.folder || "";
      if (state.folderClosed.has(key)) state.folderClosed.delete(key);
      else state.folderClosed.add(key);
      renderList(false);
    };
    el.onclick = (e) => {
      if (e.detail > 1) { clearTimeout(folderClickTimer); return; }
      clearTimeout(folderClickTimer);
      folderClickTimer = setTimeout(toggle, 220);
    };
    el.ondblclick = (e) => {
      e.preventDefault();
      clearTimeout(folderClickTimer);
      openWorkspace(el.dataset.workspace || "");
    };
    el.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    };
  }
  for (const el of list.querySelectorAll(".item")) {
    const open = () => { location.hash = "#" + encodeURIComponent(el.dataset.id); };
    el.onclick = (e) => {
      if (e.detail > 1) return;
      if (e.target.closest && e.target.closest(".acts")) return;
      open();
    };
    el.ondblclick = (e) => {
      if (e.target.closest && e.target.closest(".acts")) return;
      openSessionFolder(el.dataset.id);
    };
    el.onkeydown = (e) => {
      if (e.target !== el) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    };
  }
  for (const el of list.querySelectorAll(".folder-more")) {
    el.onclick = (e) => {
      e.stopPropagation();
      state.folderExpanded.add(el.dataset.folderMore || "");
      state.shown = rows.length;
      renderList(false);
    };
  }
  for (const el of list.querySelectorAll(".act[data-more]")) {
    el.onclick = (e) => openRowMenu(e, el.dataset.more);
  }
  const loadMore = document.getElementById("loadmore");
  if (loadMore) {
    loadMore.onclick = () => { state.shown += PAGE_SIZE; renderList(false); };
  }
  const all = state.sessions.length;
  const n = all;
  const unit = state.lang === "zh" ? "个会话"
    : (n === 1 ? "conversation" : "conversations");
  document.getElementById("count").textContent = n ? n + " " + unit : "";
}

function listNearBottom(el) {
  return el.scrollTop + el.clientHeight >= el.scrollHeight - 160;
}


const IC_CHAT = '<svg viewBox="0 0 48 48" width="42" height="42" fill="none" stroke="currentColor"'
  + ' stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">'
  + '<path d="M11 8h17l9 9v23H11z"/><path d="M27 8v10h10"/><path d="M17 24h13M17 30h13M17 36h8"/></svg>';
const IC_SEARCH = '<svg viewBox="0 0 48 48" width="42" height="42" fill="none" stroke="currentColor"'
  + ' stroke-width="1.3" stroke-linecap="round"><circle cx="22" cy="22" r="10"/><path d="M29.5 29.5L38 38"/></svg>';

function emptyState(kind) {
  const box = document.querySelector("#empty .box");
  const top = document.getElementById("top");
  top.classList.remove("hidden");
  top.classList.add("bare");
  document.getElementById("flow").classList.add("hidden");
  document.getElementById("trajectory").classList.add("hidden");
  document.getElementById("turnrail").classList.add("hidden");
  closeKidMenu();
  document.getElementById("empty").classList.remove("hidden");
  if (kind === "filtered") {
    box.innerHTML = '<div class="ic">' + IC_SEARCH + "</div><h2>" + esc(T("filteredTitle")) + "</h2>"
      + "<p>" + esc(T("filteredBody")) + "</p>";
  } else if (kind === "pick") {
    box.innerHTML = '<div class="ic">' + IC_CHAT + "</div><h2>" + esc(T("pickTitle")) + "</h2>"
      + "<p>" + esc(T("pickBody")) + "</p>";
  } else {
    box.innerHTML = '<div class="ic">' + IC_CHAT + "</div><h2>" + esc(T("emptyTitle")) + "</h2>"
      + "<p>" + esc(T("emptyBody")) + "</p><p>" + T("emptyHint") + "</p>";
  }
  
  
  renderList();
}

function closeKidMenu() {
  const menu = document.getElementById("kidmenu");
  if (menu) menu.classList.add("hidden");
}

const kidOpen = new Set();
let kidMenuRows = [];

function kidKind(k) {
  return k.kind === "fork" ? T("childKindFork") : T("childKindSubagent");
}

function kidStatus(k) {
  return k.status === "running" ? T("childRunning")
    : k.status === "completed" ? T("childCompleted") : T("childInactive");
}

function kidMeta(k) {
  const bits = [kidKind(k), kidStatus(k)];
  if (k.tokens) bits.push(fmtTokens(k.tokens) + " tok");
  if (k.duration_s) bits.push(fmtDuration(k.duration_s));
  return bits.join(" · ");
}

function kidRows(rows, depth) {
  let html = "";
  for (const k of rows) {
    const children = k.children || [];
    const has = children.length > 0;
    const open = has && kidOpen.has(k.session_id);
    const dot = k.status === "running" ? "ok" : (k.stamp === "queued" ? "warn" : "");
    html += '<div class="kidbranch">'
      + '<button class="kidrow" data-child="' + esc(k.session_id) + '" '
      + 'style="padding-left:' + (7 + depth * 16) + 'px">'
      + '<span class="kdisclosure" data-toggle="' + esc(k.session_id) + '">'
      + (has ? (open ? "▾" : "▸") : "") + "</span>"
      + '<span class="kdot ' + dot + '"></span>'
      + '<span class="kbody"><span class="klabel">' + esc(sessionTitle(k)) + "</span>"
      + '<span class="kmeta">' + esc(kidMeta(k)) + "</span></span>"
      + '<span class="kcount">' + (has ? (open ? "⌄" : "›") : "") + "</span></button>"
      + (open ? kidRows(children, depth + 1) : "")
      + "</div>";
  }
  return html;
}

function bindKidMenu(rows) {
  const menu = document.getElementById("kidmenu");
  for (const el of menu.querySelectorAll(".kdisclosure")) {
    el.onclick = (e) => {
      e.stopPropagation();
      const id = el.dataset.toggle;
      if (kidOpen.has(id)) kidOpen.delete(id);
      else kidOpen.add(id);
      renderKidMenu(rows);
    };
  }
  for (const el of menu.querySelectorAll(".kidrow")) {
    el.onclick = (e) => {
      if (e.target.closest && e.target.closest(".kdisclosure")) return;
      closeKidMenu();
      location.hash = "#" + encodeURIComponent(el.dataset.child);
    };
  }
}

function renderKidMenu(rows) {
  const menu = document.getElementById("kidmenu");
  if (!menu) return;
  kidMenuRows = rows || [];
  menu.innerHTML = kidRows(kidMenuRows, 0);
  menu.classList.remove("hidden");
  bindKidMenu(kidMenuRows);
}

function toggleKidMenu(rows) {
  const menu = document.getElementById("kidmenu");
  if (!menu) return;
  if (!menu.classList.contains("hidden")) { closeKidMenu(); return; }
  renderKidMenu(rows || []);
}


function trajKindLabel(kind) {
  if (kind === "user") return T("trajUser");
  if (kind === "assistant") return T("trajAssistant");
  if (kind === "compaction") return T("trajCompaction");
  return T("trajTool");
}

function trajSummary(r) {
  if (r.kind === "tool") {
    let text = (r.tool || "tool") + (r.args ? "  " + String(r.args).replace(/\s+/g, " ").slice(0, 140) : "");
    if (r.result) text += "  → " + String(r.result).replace(/\s+/g, " ").slice(0, 140);
    if (r.pending) text += "  · pending";
    return text;
  }
  return r.summary || trajKindLabel(r.kind);
}

function trajDetail(r) {
  const bits = [];
  if (r.kind === "tool") {
    if (r.args) bits.push("ARGS\n" + r.args);
    if (r.result) bits.push((r.is_error ? "ERROR\n" : "OUTPUT\n") + r.result);
    if (r.pending) bits.push("pending result");
  } else if (r.text) {
    bits.push(r.text);
  }
  if (!bits.length) bits.push(trajKindLabel(r.kind));
  return bits.join("\n\n");
}

function trajRowsHtml(rows) {
  let html = "";
  let turn = null;
  let step = null;
  let closedTurn = false;
  for (const r of rows) {
    if (r.turn !== turn) {
      turn = r.turn;
      step = null;
      closedTurn = false;
      if (turn != null) {
        closedTurn = state.trajClosed.has(String(turn));
        html += '<div class="trajturn" data-traj-turn="' + esc(String(turn)) + '">'
          + '<span class="tchev">' + (closedTurn ? "▸" : "▾") + "</span>"
          + esc(fill(T("trajTurn"), { n: turn }))
          + "</div>";
      }
    }
    if (closedTurn) continue;
    if (r.step !== step) {
      step = r.step;
      if (step != null) html += '<div class="trajstep">' + esc(fill(T("trajStep"), { n: step })) + "</div>";
    }
    const open = state.trajOpen.has(String(r.ts) + ":" + String(r.turn) + ":" + String(r.step) + ":" + String(r.kind) + ":" + String(r.call_id || ""));
    const key = esc(String(r.ts) + ":" + String(r.turn) + ":" + String(r.step) + ":" + String(r.kind) + ":" + String(r.call_id || ""));
    const token = r.tokens ? fmtTokens(r.tokens) + " tok" : "";
    const duration = r.duration_ms ? fmtDuration(r.duration_ms / 1000) : "";
    html += '<div class="trajrow' + (open ? " open" : "") + '" data-traj-key="' + key + '" tabindex="0">'
      + '<span class="tk ' + esc(r.kind) + '">' + esc(trajKindLabel(r.kind)) + "</span>"
      + '<span class="tc ' + esc(r.kind) + '">' + esc(trajSummary(r)) + "</span>"
      + '<span class="tt">' + esc(token) + "</span>"
      + '<span class="tm">' + esc([duration, fmtTime(r.ts)].filter(Boolean).join(" · ")) + "</span>"
      + (open ? '<div class="trajdetail">' + esc(trajDetail(r)) + "</div>" : "")
      + "</div>";
  }
  return html;
}

function renderTrajectory() {
  const box = document.getElementById("trajectory");
  const data = state.trajectory;
  if (!data) { box.innerHTML = '<div class="trajempty">' + esc(T("trajEmpty")) + "</div>"; return; }
  const all = (data.rows || []).filter((r) => {
    const q = String(state.trajQ || "").toLowerCase();
    if (!q) return true;
    return [r.text, r.summary, r.tool, r.args, r.result, r.kind]
      .some((v) => String(v || "").toLowerCase().includes(q));
  });
  const start = Math.max(0, all.length - state.trajLimit);
  const rows = all.slice(start);
  const turns = [...new Set(rows.map((r) => r.turn).filter((x) => x != null))];
  let html = '<div class="trajbar"><span>' + esc(fill(T("trajTurns"), { n: data.turn_count || turns.length })) + "</span>"
    + '<input id="trajq" class="trajsearch" autocomplete="off" placeholder="'
    + esc(T("trajSearch")) + '" value="' + esc(state.trajQ) + '">'
    + '<span class="sp"></span>'
    + '<button id="trajtoggle">' + esc(turns.length && turns.every((t) => state.trajClosed.has(String(t)))
      ? T("trajExpand") : T("trajCollapse")) + "</button></div>";
  const timed = rows.filter((r) => r.ts_ms);
  if (timed.length) {
    const min = Math.min(...timed.map((r) => r.ts_ms));
    const max = Math.max(...timed.map((r) => r.ts_ms + Math.max(r.duration_ms || 0, 50)));
    const span = Math.max(1, max - min);
    const lanes = { assistant: 0, tool: 1, user: 2, compaction: 1 };
    html += '<div class="trajtimeline"><span class="tlabel a">' + esc(T("trajAssistant")) + "</span>"
      + '<span class="tlabel b">' + esc(T("trajTool")) + "</span>"
      + '<span class="tlabel c">' + esc(T("trajUser")) + "</span>"
      + '<div class="trajlanes">';
    for (let lane = 0; lane < 3; lane++) {
      html += '<div class="trajtrack l' + lane + '">';
      for (const r of timed) {
        if ((lanes[r.kind] ?? 1) !== lane) continue;
        const left = ((r.ts_ms - min) / span) * 100;
        const width = Math.max(0.25, (Math.max(r.duration_ms || 0, 50) / span) * 100);
        html += '<span class="tl ' + esc(r.kind) + (r.is_error ? " error" : "")
          + '" style="left:' + left + '%;width:' + width + '%"></span>';
      }
      html += "</div>";
    }
    html += '</div></div><div class="trajaxis"><span>' + esc(fmtTime(new Date(min).toISOString()))
      + "</span><span>" + esc(fmtTime(new Date(max).toISOString())) + "</span></div>";
  }
  if (!rows.length) {
    html += '<div class="trajempty">' + esc(T("trajNoMatch")) + "</div>";
    box.innerHTML = html;
    const q = document.getElementById("trajq");
    if (q) q.oninput = (e) => { state.trajQ = e.target.value; renderTrajectory(); };
    return;
  }
  html += '<div class="trajhead"><span>' + esc(T("trajEvent")) + "</span><span>"
    + esc(T("trajContent")) + "</span><span>" + esc(T("trajTokens")) + "</span><span>"
    + esc(T("trajTime")) + "</span></div>";
  html += '<div class="trajledger">' + trajRowsHtml(rows) + "</div>";
  const earlierCount = Math.max(0, Number(data.total_rows || 0) - (data.rows || []).length);
  if ((start > 0 || data.truncated) && !state.trajQ) {
    html += '<button class="linkbtn" id="trajearlier">'
      + esc(fill(T("trajEarlier"), { n: earlierCount || start })) + "</button>";
  }
  box.innerHTML = html;
  const toggle = document.getElementById("trajtoggle");
  if (toggle) toggle.onclick = () => {
    if (state.trajClosed.size) state.trajClosed.clear();
    else for (const t of turns) state.trajClosed.add(String(t));
    renderTrajectory();
  };
  for (const el of box.querySelectorAll(".trajturn")) {
    el.onclick = () => {
      const key = el.dataset.trajTurn;
      if (state.trajClosed.has(key)) state.trajClosed.delete(key);
      else state.trajClosed.add(key);
      renderTrajectory();
    };
  }
  for (const el of box.querySelectorAll(".trajrow")) {
    el.onclick = (e) => {
      if (e.target.closest && e.target.closest(".trajdetail")) return;
      const key = el.dataset.trajKey;
      if (state.trajOpen.has(key)) state.trajOpen.delete(key);
      else state.trajOpen.add(key);
      renderTrajectory();
    };
  }
  const earlier = document.getElementById("trajearlier");
  if (earlier) earlier.onclick = async () => {
    state.trajLimit += 400;
    await loadTrajectory(state.current);
  };
  const q = document.getElementById("trajq");
  if (q) q.oninput = (e) => {
    state.trajQ = e.target.value;
    renderTrajectory();
    const box = document.getElementById("trajectory");
    const next = document.getElementById("trajq");
    if (next) { next.focus(); next.setSelectionRange(next.value.length, next.value.length); }
  };
}

async function loadTrajectory(s) {
  const box = document.getElementById("trajectory");
  const sid = s.session_id;
  box.innerHTML = '<div class="trajempty">' + esc(T("trajLoading")) + "</div>";
  try {
    const data = await apiJson("api/records/session/" + encodeURIComponent(sid)
      + "/trajectory?limit=" + state.trajLimit);
    if (!state.current || state.current.session_id !== sid) return;
    state.trajectory = data;
    state.trajSid = sid;
    renderTrajectory();
  } catch (e) {
    if (state.current && state.current.session_id === sid) {
      box.innerHTML = '<div class="trajempty">' + esc(e.message || T("trajEmpty")) + "</div>";
    }
  }
}

function setView(view) {
  state.view = view === "trajectory" ? "trajectory" : "chat";
  for (const el of document.querySelectorAll("#viewtabs button")) {
    el.classList.toggle("on", el.dataset.view === state.view);
    el.setAttribute("aria-selected", el.dataset.view === state.view ? "true" : "false");
  }
  const flow = document.getElementById("flow");
  const traj = document.getElementById("trajectory");
  if (!state.current) return;
  if (state.view === "trajectory") {
    flow.classList.add("hidden");
    document.getElementById("turnrail").classList.add("hidden");
    traj.classList.remove("hidden");
    if (!state.trajectory || state.trajSid !== state.current.session_id) {
      loadTrajectory(state.current);
    } else {
      renderTrajectory();
    }
  } else {
    traj.classList.add("hidden");
    flow.classList.remove("hidden");
    requestAnimationFrame(renderTurnRail);
  }
}

function showSessionHead(s) {
  document.getElementById("empty").classList.add("hidden");
  const top = document.getElementById("top");
  top.classList.remove("hidden");
  top.classList.remove("bare");
  document.getElementById("flow").classList.remove("hidden");
  document.getElementById("title").textContent = sessionTitle(s);
  document.getElementById("title").title = sessionTitle(s);
  const capture = document.getElementById("capture");
  const tier = s.capture_tier || "unknown";
  capture.hidden = false;
  capture.className = "capbadge session-only " + (tier === "captured" || tier === "ingested" ? tier : "unknown");
  capture.textContent = tier === "captured" ? T("captureCaptured")
    : tier === "ingested" ? T("captureIngested") : T("captureUnknown");
  capture.title = tier === "captured" ? T("captureCapturedHint")
    : tier === "ingested" ? T("captureIngestedHint") : T("captureUnknownHint");
  const isFork = s.kind === "fork";
  
  
  const bits = [];
  if (s.started_at) bits.push(fmtTime(s.started_at));
  if (!isFork && s.status) bits.push(kidStatus(s));
  if (!isFork && s.tokens) bits.push(fmtTokens(s.tokens) + " tok");
  if (!isFork && s.duration_s) bits.push(fmtDuration(s.duration_s));
  document.getElementById("meta").textContent = [agentLabel(s.agent), localDay(s.started_at), fill(T("turnsTalk"), {n:s.turns || 0})].filter(Boolean).join(" · ");

  
  
  
  const stampPill = document.getElementById("stamp");
  const stampState = s.stamp || "unstamped";
  const stampNone = stampState !== "stamped" && stampState !== "queued";
  const partial = !!s.stamp_partial;
  const detail = s.stamp_detail || {};
  const covers = Number(detail.covers_up_to || s.stamp_covers || 0);
  const total = Number(s.records || 0);
  const tsa = tsaLabel(detail.provider);
  stampPill.hidden = false;
  stampPill.className = "pill session-only "
    + (stampNone ? "none" : (stampState === "queued" || partial ? "wait" : "ok"));
  stampPill.textContent = stampNone ? T("stampNone")
    : (stampState === "queued" ? T("stampWait")
      : partial ? T("stampPartial") : T("stampOk"))
      + (tsa && stampState !== "queued" ? " · " + tsa : "");
  
  
  
  const sealSum = sealSummaryHTML(s);
  if (sealSum) {
    stampPill.className = "pill2 session-only";
    stampPill.innerHTML = sealSum;
  }
  const title = [];
  if (stampNone) title.push(T("stampNoneHint"));
  else {
    if (tsa) title.push(tsa);
    
    if (s.turns && s.turns_stamped != null) {
      title.push(fill(T("turnsOf"), { a: s.turns_stamped, b: s.turns }));
    }
    if (detail.gen_time) title.push(String(detail.gen_time));
  }
  stampPill.title = title.join(" · ") || (stampNone ? T("stampNoneHint") : T("stampOk"));
  stampPill.setAttribute("aria-label",
    stampPill.textContent + (title.length ? " · " + title.join(" · ") : ""));
  stampPill.onclick = () => toggleDrawer(true);

  closeKidMenu();
  const kids = s.children || [];
  const kidBtn = document.getElementById("children");
  if (kids.length) {
    kidBtn.hidden = false;
    kidBtn.textContent = fill(T("childrenCount"), { n: kids.length }) + " ⌄";
    kidBtn.onclick = (e) => { e.stopPropagation(); toggleKidMenu(kids); };
  } else {
    kidBtn.hidden = true;
    kidBtn.textContent = "";
    kidBtn.onclick = null;
  }
  const sibs = isFork ? [] : (s.siblings || []);
  const sibBtn = document.getElementById("siblings");
  if (sibs.length) {
    sibBtn.hidden = false;
    sibBtn.textContent = fill(T("siblingsCount"), { n: sibs.length }) + " ⌄";
    sibBtn.onclick = (e) => { e.stopPropagation(); toggleKidMenu(sibs); };
  } else {
    sibBtn.hidden = true;
    sibBtn.onclick = null;
  }
  const parentId = isFork ? "" : (s.parent_session || s.fork_of || "");
  const parentBtn = document.getElementById("parent");
  if (parentId) {
    parentBtn.hidden = false;
    parentBtn.textContent = T("childParent");
    parentBtn.title = s.parent_title || parentId;
    parentBtn.onclick = () => { location.hash = "#" + encodeURIComponent(parentId); };
  } else {
    parentBtn.hidden = true;
    parentBtn.onclick = null;
  }

  const nestedChild = !!s.nested_child || !!s.nested_fork;
  const delButton = document.getElementById("del");
  const exportButton = document.getElementById("export");
  delButton.style.display = nestedChild ? "none" : "";
  exportButton.style.display = nestedChild ? "none" : "";
  delButton.onclick = nestedChild ? null : () => askDelete(s.session_id);
  document.getElementById("info").onclick = () => toggleDrawer(true);
  exportButton.onclick = nestedChild ? null : () => exportSession(s.session_id);

  if (state.trajSid !== s.session_id) {
    state.trajSid = s.session_id;
    state.trajectory = null;
    state.trajOpen.clear();
    state.trajClosed.clear();
  }
  setView(state.view);
}

function toolVerb(name) {
  const n = String(name || "").toLowerCase();
  if (/(^|_)(bash|shell|exec|run|terminal)/.test(n) || n === "bash") return "run";
  if (/^(read|cat|view)/.test(n)) return "read";
  if (/^(write|edit|apply|create|patch)/.test(n)) return "edit";
  if (/search/.test(n)) return "search";
  if (/(fetch|browse|open_url|http)/.test(n)) return "fetch";
  if (/subagent|agent|task/.test(n)) return "agent";
  if (/job/.test(n)) return "job";
  if (/todo|plan/.test(n)) return "todo";
  if (/^(glob|grep|find|ls)/.test(n)) return "code";
  return "other";
}
const VERBS = {
  zh: { run: "执行了命令", read: "读取了文件", edit: "修改了文件", search: "搜索了网页",
        fetch: "访问了网页", agent: "调用了子智能体", job: "查看了后台任务",
        todo: "更新了任务",
        code: "检索了文件", other: "调用了工具" },
  en: { run: "ran commands", read: "read files", edit: "edited files", search: "searched the web",
        fetch: "opened pages", agent: "used a subagent", job: "checked background jobs",
        todo: "updated the plan",
        code: "searched files", other: "used tools" },
};
function toolCats(tools) {
  const kinds = [];
  for (const t of tools) { const v = toolVerb(t.name); if (!kinds.includes(v)) kinds.push(v); }
  const pack = VERBS[state.lang] || VERBS.zh;
  
  const named = kinds.filter((k) => k !== "other");
  return (named.length ? named : kinds).map((k) => pack[k]);
}

function toolDetail(t, sid) {
  const out = t.output || "";
  const lines = out ? out.split("\n").length : 0;
  const input = t.input || "";
  return '<details class="tool' + (t.is_error ? " err" : "") + '"><summary>'
    + '<span class="toolchev">' + (t.is_error ? "✕" : "›") + '</span>'
    + '<span class="s">' + esc(t.name + (t.summary ? " · " + t.summary : "")) + '</span>'
    + '<span class="n">' + (out ? lines + (state.lang === "zh" ? " 行" : " lines") : "")
    + '</span></summary>'
    + (input ? '<pre>' + esc(input) + '</pre>' : "")
    + (out ? '<pre class="out">' + esc(out) + '</pre>' : "")
    + ((t.images || []).length
        ? '<div class="imgs toolimgs">' + (t.images || []).map((img) =>
            imgTag(sid, img, "", false, (t.images || []).length > 1 ? "tile" : "single")).join("") + "</div>"
        : "")
    + "</details>";
}

const ICON = {
  think: '<path d="M8 2.2l1.4 3.4L13 7l-3.6 1.4L8 11.8 6.6 8.4 3 7l3.6-1.4z"/>',
  run: '<rect x="1.6" y="2.6" width="12.8" height="10.8" rx="2"/><path d="M4 6.2l2 1.9-2 1.9M8.2 10h4"/>',
  read: '<path d="M4 2.2h5.2l2.8 2.8v8.8H4z"/><path d="M9.2 2.2V5h2.8"/>',
  edit: '<path d="M3 13l.9-2.8 6.8-6.8 2 2-6.8 6.7z"/>',
  search: '<circle cx="7" cy="7" r="3.8"/><path d="M10 10l3.6 3.6"/>',
  fetch: '<circle cx="8" cy="8" r="5.8"/><path d="M2.2 8h11.6M8 2.2c1.8 2.1 1.8 9.5 0 11.6M8 2.2c-1.8 2.1-1.8 9.5 0 11.6"/>',
  agent: '<circle cx="5.2" cy="5.8" r="2.4"/><circle cx="10.8" cy="10.2" r="2.4"/><path d="M7.2 6.6l2.4 2.2"/>',
  plan: '<path d="M3 5h10M3 8h10M3 11h5.5"/>',
  other: '<circle cx="8" cy="8" r="2.1"/><path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2"/>',
};
function disc(kind) {
  return '<span class="k"><span class="ico"><svg viewBox="0 0 16 16">'
    + (ICON[kind] || ICON.other) + '</svg></span>'
    + '<span class="chev">›</span></span>';
}
function thinkRow(text) {
  const one = text.replace(/\s+/g, " ").slice(0, 90);
  return '<details class="row"><summary>' + disc("think")
    + '<span class="s">' + esc(T("thinking")) + " · " + esc(one) + '</span>'
    + '<span class="n">' + text.length + (state.lang === "zh" ? " 字" : " chars")
    + '</span></summary><div class="think">' + md(text, {images: false}) + "</div></details>";
}

function fitImage(width, height) {
  
  const ratio = Math.min(4, Math.max(0.25, width / height));
  const box = ratio >= 1 ? { w: 240, h: 240 / ratio } : { w: 240 * ratio, h: 240 };
  const scale = Math.min(1, width / box.w, height / box.h);
  return { w: Math.max(1, Math.round(box.w * scale)), h: Math.max(1, Math.round(box.h * scale)) };
}

function imgTag(sid, img, extra, eager, variant) {
  if (!img || !img.ok) {
    return '<span class="imgmiss">' + esc(T("imageMissing")) + "</span>";
  }
  const src = BASE + "api/records/session/" + encodeURIComponent(sid)
    + "/attachment/" + encodeURIComponent(img.file);
  let dw = Number(img.width) || 0;
  let dh = Number(img.height) || 0;
  if (variant === "tile") { dw = 64; dh = 64; }
  else if (variant === "single" && dw && dh) {
    const fit = fitImage(dw, dh);
    dw = fit.w; dh = fit.h;
  }
  return '<a class="shotlink" href="' + src + '" target="_blank" rel="noreferrer" title="'
    + esc(img.name || "") + '"><img class="shot' + (extra ? " " + extra : "")
    + (variant ? " " + variant : "")
    + '" loading="' + (eager ? "eager" : "lazy") + '"'
    + (dw && dh ? ' width="' + dw + '" height="' + dh + '"' : "")
    + ' src="' + src + '" alt="'
    + esc(img.name || "") + '" data-fallback="' + esc(T("imageBroken")) + '"></a>';
}
let lightboxReturn = null;
function openLightbox(src, alt) {
  const lb = document.getElementById("lightbox");
  if (!lb || !src) return;
  lightboxReturn = document.activeElement instanceof HTMLElement
    ? document.activeElement : null;
  const img = lb.querySelector(".lbimg");
  img.src = src;
  img.alt = alt || "";
  const close = lb.querySelector(".lbclose");
  close.title = T("close");
  close.setAttribute("aria-label", T("close"));
  lb.hidden = false;
  document.body.classList.add("lb-open");
  close.focus();
}

function closeLightbox() {
  const lb = document.getElementById("lightbox");
  if (!lb || lb.hidden) return;
  lb.hidden = true;
  const img = lb.querySelector(".lbimg");
  img.removeAttribute("src");
  img.alt = "";
  document.body.classList.remove("lb-open");
  if (lightboxReturn && lightboxReturn.focus) lightboxReturn.focus();
  lightboxReturn = null;
}

const COPY_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true">'
  + '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/>'
  + '<path d="M10.5 5.5V4A1.5 1.5 0 0 0 9 2.5H4A1.5 1.5 0 0 0 2.5 4v5A1.5 1.5 0 0 0 4 10.5h1.5"/></svg>';
const CHECK_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true">'
  + '<path d="M3 8.2l3.1 3.1L13 4.5"/></svg>';
function copyBtn(text) {
  return '<button class="copybtn" type="button" data-copy="' + esc(text)
    + '" title="' + esc(T("copy")) + '" aria-label="' + esc(T("copy")) + '">'
    + COPY_ICON + "</button>";
}
function msgActions(text, ts) {
  return '<div class="msg-actions"><span>' + esc(fmtTime(ts)) + "</span>"
    + (text ? copyBtn(text) : "") + "</div>";
}
function bindCopyButtons() {
  for (const el of document.querySelectorAll(".copybtn[data-copy]")) {
    el.onclick = (e) => {
      e.stopPropagation();
      copyText(el.dataset.copy || "", true).then((ok) => {
        if (!ok) { toast(T("copyFail")); return; }
        el.classList.add("copied");
        el.innerHTML = CHECK_ICON;
        el.setAttribute("aria-label", T("copied"));
        clearTimeout(el._copyTimer);
        el._copyTimer = setTimeout(() => {
          el.classList.remove("copied");
          el.innerHTML = COPY_ICON;
          el.setAttribute("aria-label", T("copy"));
        }, 1400);
      });
    };
  }
  for (const el of document.querySelectorAll(".filelink[data-file]")) {
    el.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        await api("api/records/session/" + encodeURIComponent(state.mdSid)
          + "/reveal-path?path=" + encodeURIComponent(el.dataset.file || ""),
          { method: "POST" });
        toast(T("fileOpened"));
      } catch (err) {
        toast(T("fileOpenFail") + " " + (err.message || ""));
      }
    };
  }
}


const IC_SEAL = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8l5 1.9v3.9c0 3-2.1 5.4-5 6.6-2.9-1.2-5-3.6-5-6.6V3.7z"/><path d="M5.8 8.2l1.6 1.6 2.9-3.2"/></svg>';
function sealPending(s) { return (s.stamps || []).some((x) => x.pending); }
function sealChip(ok, text) {
  return '<span class="stchip' + (ok ? " ok" : "") + '">' + (ok ? IC_SEAL : '<i class="no"></i>')
    + "<span>" + esc(text) + "</span></span>";
}


function sealMarks(s) { return Array.isArray(s.turn_marks) && s.turn_marks.length ? s.turn_marks : null; }
function sealSummaryHTML(s) {
  const marks = sealMarks(s);
  if (!marks) return "";
  const ok = marks.filter(Boolean).length, no = marks.length - ok;
  
  
  
  return sealChip(ok > 0, fill(T("sealOkN"), { n: ok }))
    + sealChip(false, fill(T("sealNoN"), { n: no })
               + (no && sealPending(s) ? " (" + T("sealWaiting") + ")" : ""));
}
function markWord(s, mark) {
  if (mark) { const p = tsaLabel(mark.provider); return T("sealOk") + (p ? " · " + p : ""); }
  return T("sealNo") + (sealPending(s) ? " · " + T("sealWaiting") : "");
}
function localDay(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}
function docHeadHTML(s) {
  const who = [agentLabel(s.agent), s.started_at ? localDay(s.started_at) : ""].filter(Boolean).join(" · ");
  const sum = sealSummaryHTML(s);
  return '<header class="dochead"><h1>' + esc(sessionTitle(s)) + '</h1>' + (sessionTitle(s).length > 65 ? '<button class="title-toggle">' + (state.lang==="zh"?"展开标题":"Show more") + '</button>' : '') + '<div class="m"><span>'
    + esc(who) + "</span>" + (s.turns ? "<span>" + esc(fill(T("turnsTalk"), { n: s.turns })) + "</span>" : "")
    + "</div>" + (sum ? '<div class="docsum">' + sum + "</div>" : "") + "</header>";
}
function turnOpenHTML(s, marks, n) {
  const known = !!marks && n <= marks.length;
  const mark = known ? marks[n - 1] : null;
  const chip = known ? sealChip(!!mark, markWord(s, mark)) : "";
  const no = esc(fill(T("turnNo"), { n: n }));
  return '<section class="turnsec" id="turn-' + n + '" data-turn="' + n + '"'
    + (known ? ' data-st="' + (mark ? "ok" : "none") + '"' : "") + ">"
    + (known ? '<button type="button" class="tmark" data-evopen title="' + esc(T("evOpenHint")) + '">'
        + chip + '<span class="tno">' + no + "</span></button>" : "")
    + '<div class="twho"><b>' + esc(T("you")) + "</b><span>" + no + "</span>"
    + (known ? '<button type="button" class="tinl" data-evopen>' + chip + "</button>" : "")
    + '<span class="line"></span></div>';
}


function turnBoundHTML(s, marks, n) {
  if (!marks || n > marks.length) return "";
  const cur = marks[n - 1], next = n < marks.length ? marks[n] : null;
  let html = "";
  
  
  
  const same = (x, y) => !!x && !!y && tsaLabel(x.provider) === tsaLabel(y.provider);
  if (cur && (n === marks.length || !same(cur, next))) {
    let a = n;
    while (a > 1 && same(marks[a - 2], cur)) a--;
    const vals = { n: n, a: a, b: n, p: tsaLabel(cur.provider) || "TSA", t: fmtDateTime(cur.gen_time) };
    const text = a === 1 ? fill(T("boundOkFirst"), vals)
      : a === n ? fill(T("boundOkOne"), vals) : fill(T("boundOkRange"), vals);
    html += '<div class="tbound ok">' + IC_SEAL + "<span>" + esc(text) + "</span></div>";
  }
  if (cur && n < marks.length && !next) {
    const wait = sealPending(s);
    html += '<div class="tbound">' + (wait ? '<span class="tspin"></span>' : "") + "<span>"
      + esc(fill(T("boundNo"), { n: marks.length - n, why: T(wait ? "boundNoWait" : "boundNoWhy") }))
      + "</span></div>";
  }
  return html;
}
function turnInfoHTML(s, n) {
  const marks = sealMarks(s);
  const mark = marks ? marks[n - 1] : null;
  const fact = (k, v, cls) => '<div class="tfact"><span class="k">' + esc(k) + '</span><span class="v'
    + (cls ? " " + cls : "") + '">' + v + "</span></div>";
  const why = mark
    ? fill(T("evWhyOk"), { p: tsaLabel(mark.provider) || "TSA", t: fmtDateTime(mark.gen_time) })
    : T(sealPending(s) ? "evWhyWait" : "evWhyNone");
  const tier = s.capture_tier || "unknown";
  const tierName = tier === "captured" ? T("captureCaptured")
    : tier === "ingested" ? T("captureIngested") : T("captureUnknown");
  const tierHint = tier === "captured" ? T("captureCapturedHint")
    : tier === "ingested" ? T("captureIngestedHint") : T("captureUnknownHint");
  return '<div class="th"><span>' + esc(fill(T("evTitle"), { n: n }))
    + '</span><button type="button" class="x" data-evopen>' + esc(T("evClose")) + "</button></div>"
    + fact(T("evState"), "<b>" + esc(markWord(s, mark)) + "</b> " + esc(why), mark ? "ok" : "")
    + fact(T("evSig"), esc(T("evSigText")))
    + fact(tierName, esc(tierHint));
}





function outlineTitle(text, n) {
  const MAX = 30;
  const pasted = [];
  const body = String(text || "").replace(
    /^[ \t]*<pasted_content\b[^>]*>[ \t]*\n([\s\S]*?)\n[ \t]*<\/pasted_content\b[^>]*>[ \t]*$/gm,
    (m, inner) => { pasted.push(inner); return ""; }).trim();
  const stripFences = (x) => x.replace(/^\s*(`{3,}|~{3,})[^\n]*\n[\s\S]*?^\s*\1[^\n]*$/gm, " ");
  const stripNoise = (x) => x.replace(/@"[^"]*"|@\S+/g, " ").replace(/https?:\/\/\S+/g, " ")
    .replace(/(?:~|\.{1,2})?\/[^\s，。？！；]*\/[^\s，。？！；]*/g, " ");
  const unmark = (x) => x.replace(/^\s*(#{1,6}|[-*>]|\d+[.)])\s+/, "").replace(/[*`_]+/g, "")
    .replace(/\s+/g, " ").trim();
  const cut = (x) => (x.length > MAX ? x.slice(0, MAX - 1).replace(/[，,、：:\s]+$/, "") + "…" : x);
  if (pasted.length) {
    let fence = null, head = "";
    for (const line of pasted[0].split("\n")) {
      const f = line.match(/^\s*(`{3,}|~{3,})/);
      if (f) { if (!fence) fence = f[1]; else if (f[1][0] === fence[0] && f[1].length >= fence.length) fence = null; continue; }
      if (fence) continue;
      const h = line.match(/^(#{1,3})\s+(.*)/);
      if (h && unmark(h[2])) { head = unmark(h[2]); break; }
    }
    if (head) return cut(head);
  }
  const plain = stripNoise(stripFences(body));
  const clean = unmark(plain.replace(/\n+/g, " "));
  if (!clean) {
    
    const raw = unmark(stripFences(body).replace(/\n+/g, " "));
    return raw ? cut(raw) : fill(T("turnNoText"), { n: n });
  }
  if (clean.length <= 12) return clean;
  const sentences = plain.match(/[^。？！?!；;\n]+[。？！?!；;]?/g) || [clean];
  const first = sentences.map(unmark).find((x) => x.replace(/[^\w一-龥]/g, "").length >= 4) || clean;
  const polite = /^(?:(?:好的?|嗯|ok|那)[，,\s]*)?(?:请你|请|麻烦你?|帮我|帮忙)\s*/i;
  return cut(first.replace(polite, "").replace(/[。；;]$/, "")) || cut(clean);
}
let outlineWatch = null;
function renderOutline(s) {
  const box = document.getElementById("outline");
  const flow = document.getElementById("flow");
  if (!box || !flow) return;
  if (outlineWatch) { outlineWatch.disconnect(); outlineWatch = null; }
  const secs = [...flow.querySelectorAll(".turnsec")];
  if (!secs.length) { box.innerHTML = ""; return; }
  const users = (s.messages || []).slice(0, state.msgLimit).filter((m) => m.role === "user");
  const marks = sealMarks(s);
  box.innerHTML = '<div class="ohead">' + esc(T("outlineTitle")) + "</div>" + secs.map((sec, i) => {
    const n = i + 1, known = !!marks && n <= marks.length;
    return '<a data-turn="' + n + '"><span class="n">' + n + '</span><span class="tx">'
      + esc(outlineTitle((users[i] || {}).text || "", n)) + "</span>"
      + (known ? '<span class="od' + (marks[i] ? " ok" : "") + '"></span>' : "") + "</a>";
  }).join("");
  if (!("IntersectionObserver" in window)) return;
  outlineWatch = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (!en.isIntersecting) continue;
      for (const a of box.querySelectorAll("a[data-turn]")) {
        a.classList.toggle("on", a.dataset.turn === en.target.dataset.turn);
      }
    }
  }, { root: flow, rootMargin: "-10% 0px -75% 0px" });
  for (const sec of secs) outlineWatch.observe(sec);
}

function fileSize(n) {
  if (typeof n !== "number" || !(n >= 0)) return "";
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}


function fileRows(files) {
  return (files || []).map((f) => {
    const kind = String(f.media || "").split("/").pop().toUpperCase();
    const meta = [kind, fileSize(f.bytes)].filter(Boolean).join(" · ");
    const tip = [f.path || f.name, f.sha256 ? "SHA-256 " + f.sha256 : "",
                 T("fileListed")].filter(Boolean).join("\n");
    return '<div class="filerow" title="' + esc(tip) + '"><span class="fn">'
      + esc(f.name || f.path || "") + "</span>"
      + (meta ? '<span class="fm">' + esc(meta) + "</span>" : "") + "</div>";
  }).join("");
}
const expandedInputs = new Set();
function userBubble(m, sid) {
  const variant = (m.images || []).length > 1 ? "tile" : "single";
  const imgs = (m.images || []).map((i) => imgTag(sid, i, "", true, variant)).join("");
  const files = fileRows(m.files);
  return '<div class="msg user"><div class="bubblewrap">'
    + (imgs ? '<div class="imgs">' + imgs + "</div>" : "")
    + (files ? '<div class="files">' + files + "</div>" : "")
    + (m.text ? '<div class="bubble">' + md(m.text) + "</div>" : "")
    + msgActions(m.text || "", m.ts)
    + "</div></div>";
}

function fmtProcessDuration(seconds) {
  
  
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor(total / 60) % 60;
  const s = total % 60;
  if (state.lang === "zh") {
    let out = "";
    if (h > 0) out += h + "小时";
    if (total >= 60) out += m + "分";
    return out + s + "秒";
  }
  let out = "";
  if (h > 0) out += h + "h ";
  if (total >= 60) out += m + "m ";
  return (out + s + "s").trim();
}
function processParts(items, answerIndex) {
  
  
  
  const parts = [];
  let current = { kind: "group", tools: [], thinking: [], members: [] };
  const flush = () => {
    if (current.members.length) parts.push(current);
    current = { kind: "group", tools: [], thinking: [], members: [] };
  };
  for (let i = 0; i < items.length; i++) {
    const m = items[i];
    if (m.thinking) {
      current.thinking.push(m.thinking);
      current.members.push({ kind: "think", text: m.thinking });
    }
    if ((m.tool_calls || []).length) {
      current.tools.push(...m.tool_calls);
      current.members.push({ kind: "tools", tools: m.tool_calls });
    }
    if ((m.text || "").trim()) {
      if (i === answerIndex) {
        flush();
      } else {
        flush();
        parts.push({ kind: "text", text: m.text });
      }
    }
  }
  flush();
  return parts;
}

function processLabel(group) {
  const parts = [];
  if (group.thinking.length) parts.push(T("thinking"));
  for (const verb of toolCats(group.tools)) parts.push(verb);
  let label = parts.slice(0, 3).join(state.lang === "zh" ? "、" : ", ");
  if (state.lang !== "zh" && label) label = label.charAt(0).toUpperCase() + label.slice(1);
  return label || T("turnProcess");
}

function stepProcessGroup(group, sid) {
  let body = "";
  for (const member of group.members) {
    if (member.kind === "think") body += thinkRow(member.text);
    else if (member.kind === "tools") {
      body += member.tools.map((t) => toolDetail(t, sid)).join("");
    }
  }
  if (!body) return "";
  return '<details class="stepgroup"><summary>'
    + '<span class="chev">›</span><span class="s">' + esc(processLabel(group)) + "</span>"
    + "</summary><div class=\"stepbody\">" + body + "</div></details>";
}

function turnStatusHTML(meta) {
  const reason = String((meta && meta.reason) || "");
  const stopped = reason === "aborted" || reason === "interrupted" || reason === "cancelled";
  const failed = reason === "error";
  if (!stopped && !failed) return "";
  return '<div class="turnstatus' + (failed ? " failed" : "") + '">'
    + esc(failed ? T("turnFailed") : T("turnStopped")) + "</div>";
}

function wrapUpCard(tools, nextUser) {
  const tool = (tools || [])[tools.length - 1];
  if (!tool) return "";
  let data = {};
  try { data = JSON.parse(tool.input || "{}"); } catch (e) { return ""; }
  const recap = String(data.recap || "").trim();
  const ask = String(data.ask || "").trim();
  const actions = Array.isArray(data.actions)
    ? data.actions.map((a) => String((a && a.label) || "")).filter(Boolean) : [];
  if (!recap && !ask) return "";
  const chosen = actions.find((label) => label.trim() === String(nextUser || "").trim());
  const shown = chosen ? [chosen] : actions;
  let html = '<div class="wrapcard">';
  if (recap) html += '<div class="wrecap">' + esc(recap) + "</div>";
  if (ask) html += '<div class="wask">' + esc(ask) + "</div>";
  if (shown.length) {
    html += '<div class="wacts">' + shown.map((label) =>
      '<span class="wact' + (chosen && label === chosen ? " on" : "") + '">'
      + esc(label) + "</span>").join("") + "</div>";
  }
  return html + "</div>";
}

function aiGroup(group, agent) {
  const items = group.items || [];
  const wrapUps = items.flatMap((m) => (m.tool_calls || [])
    .filter((t) => t.name === "mcp__ccd_turn__wrap_up"));
  const visibleItems = items.map((m) => ({
    ...m,
    tool_calls: (m.tool_calls || []).filter((t) => t.name !== "mcp__ccd_turn__wrap_up"),
  }));
  
  
  
  
  
  let answerIndex = -1;
  for (let i = items.length - 1; i >= 0; i--) {
    const m = items[i];
    if ((m.text || "").trim()) {
      answerIndex = i;
      break;
    }
  }
  const answer = answerIndex >= 0 ? items[answerIndex] : null;
  const parts = processParts(visibleItems, answerIndex);
  const meta = ((state.current && state.current.host_turns) || {})[String(group.turn)] || null;
  const reason = String((meta && meta.reason) || "");
  const stopped = reason === "aborted" || reason === "interrupted" || reason === "cancelled";
  const failed = reason === "error";
  const startMs = meta && Number.isFinite(meta.start)
    ? meta.start : Date.parse(group.startTs || (items[0] && items[0].ts) || "");
  const endMs = meta && Number.isFinite(meta.end)
    ? meta.end : Date.parse(((answer || items[items.length - 1] || {}).ts) || "");
  const seconds = !stopped && !failed && Number.isFinite(startMs) && Number.isFinite(endMs)
    ? Math.max(1, (endMs - startMs) / 1000) : 0;
  const done = seconds > 0 ? fill(T("turnProcessDone"), { d: fmtProcessDuration(seconds) })
    : T("turnProcessNoTime");
  const actionLabel = processLabel({thinking: [], tools:
    visibleItems.flatMap((m) => m.tool_calls || [])});
  const baseLabel = stopped ? T("turnStopped")
    : failed ? T("turnFailed")
    : answer ? done : T("turnProcess");
  const label = actionLabel ? baseLabel + " · " + actionLabel : baseLabel;
  const open = !answer || items.length <= 8;
  const key = group.key || "";
  let body = !parts.length ? turnStatusHTML(meta) : "";
  if (parts.length) {
    body += '<button type="button" class="turnproc-toggle' + (open ? " open" : "")
      + '" data-proc-toggle="' + esc(key) + '" aria-expanded="' + (open ? "true" : "false")
      + '"><span class="chev">›</span><span class="s">' + esc(label) + "</span></button>";
    for (const part of parts) {
      const inner = part.kind === "group"
        ? stepProcessGroup(part, group.sid)
        : '<div class="proc-text proc-narration">' + md(part.text) + "</div>";
      body += '<div class="procseg" data-proc-body="' + esc(key) + '"'
        + (open ? "" : " hidden") + ">" + inner + "</div>";
    }
  }
  if (answer) body += md(answer.text);
  body += wrapUpCard(wrapUps, group.nextUser || "");
  return '<div class="msg ai"><div class="body">' + body + "</div>"
    + (answer ? msgActions(answer.text, answer.ts) : "") + "</div>";
}
function renderMessages(s, preserveScroll) {
  state.mdSid = s.session_id;
  const flow = document.getElementById("flow");
  const keepScroll = preserveScroll ? flow.scrollTop : 0;
  
  
  
  const flowTop = flow.getBoundingClientRect().top;
  const anchorItems = () => [...flow.querySelectorAll(".msg.user, .msg.ai")];
  let anchorIndex = -1;
  let anchorDelta = 0;
  if (preserveScroll) {
    const items = anchorItems();
    const el = items.find((e) => e.getBoundingClientRect().bottom > flowTop + 1);
    if (el) {
      anchorIndex = items.indexOf(el);
      anchorDelta = el.getBoundingClientRect().top - flowTop;
    }
  }
  const parts = [];
  const allMessages = s.messages || [];
  const shownMessages = allMessages.slice(0, state.msgLimit);
  const totalMessages = Number(s.messages_total || allMessages.length);
  const remainingMessages = Math.max(0, totalMessages - shownMessages.length);
  if (s.head_omitted) {
    parts.push('<div class="banner">' + esc(T("headOmitted")) + "</div>");
  }
  for (const g of (s.gaps || [])) {
    let text;
    if (g.open) text = T("gapOpen");
    else if (g.unavailable) text = T("gapUnavailable");
    else {
      const span = (g.from_turn != null && g.to_turn != null)
        ? fill(T("gapTurns"), { a: g.from_turn, b: g.to_turn })
        : fill(T("gapSeqs"), { a: g.from_host_seq, b: g.to_host_seq });
      text = fill(T("gapBanner"), { span: span });
    }
    parts.push('<div class="banner">' + esc(text) + "</div>");
  }
  let group = null;
  let groupNo = 0;
  let lastUserTs = "";
  let lastUserTurn = null;
  const seenTurns = new Set();
  
  
  const marks = sealMarks(s);
  let turnNo = 0;
  let turnOpen = false;
  const closeTurn = () => {
    if (!turnOpen) return;
    turnOpen = false;
    parts.push('<div class="tinfo" hidden></div></section>' + turnBoundHTML(s, marks, turnNo));
  };
  const flush = (nextUser) => {
    if (group) {
      if (nextUser != null) group.nextUser = nextUser;
      if (typeof group.turn === "number") seenTurns.add(group.turn);
      parts.push(aiGroup(group, s.agent));
      group = null;
    }
  };
  for (const m of shownMessages) {
    if (m.role === "user") {
      flush(m.text || "");
      closeTurn();
      turnNo++;
      parts.push(turnOpenHTML(s, marks, turnNo));
      turnOpen = true;
      lastUserTs = m.ts || "";
      lastUserTurn = typeof m.turn === "number" ? m.turn : null;
      parts.push(userBubble(m, s.session_id));
      continue;
    }
    if (!group) group = { model: m.model, ts: m.ts, startTs: lastUserTs || m.ts,
                          turn: typeof m.turn === "number" ? m.turn : lastUserTurn,
                          items: [], sid: s.session_id,
                          key: s.session_id + ":" + (groupNo++) };
    if (!group.model && m.model) group.model = m.model;
    group.items.push(m);
  }
  flush();
  
  
  const firstVisibleTurn = shownMessages.find((m) => typeof m.turn === "number")?.turn;
  const lastVisibleTurn = [...shownMessages].reverse()
    .find((m) => typeof m.turn === "number")?.turn;
  const orphan = Object.entries(s.host_turns || {})
    .filter(([turn, meta]) => !seenTurns.has(Number(turn))
      && (firstVisibleTurn == null || Number(turn) >= firstVisibleTurn)
      && (lastVisibleTurn == null || Number(turn) <= lastVisibleTurn)
      && turnStatusHTML(meta))
    .sort((a, b) => Number(a[0]) - Number(b[0]));
  for (const [, meta] of orphan) parts.push(turnStatusHTML(meta));
  closeTurn();
  if (remainingMessages > 0) {
    parts.push('<button class="linkbtn" id="moreMessages">'
      + esc(fill(T("laterMessages"), { n: remainingMessages })) + "</button>");
  }
  flow.innerHTML = '<div class="col">' + parts.join("") + "</div>";
  flow.querySelectorAll('.msg.user .bubble').forEach((bubble, index) => {
    const key = s.session_id + ':' + index;
    if (bubble.scrollHeight <= 260) return;
    
    
    const preview = document.createElement('div');
    preview.className = 'input-preview';
    preview.textContent = bubble.innerText;
    const full = document.createElement('div');
    full.className = 'input-full';
    while (bubble.firstChild) full.appendChild(bubble.firstChild);
    bubble.append(full, preview);
    const button = document.createElement('button');
    button.className = 'input-toggle';
    button.type = 'button';
    const update = () => {
      const expanded = expandedInputs.has(key);
      full.hidden = !expanded;
      preview.hidden = expanded;
      bubble.classList.toggle('folded', !expanded);
      button.setAttribute('aria-expanded', String(expanded));
      button.textContent = state.lang === 'zh' ? (expanded ? '收起' : '展开全文') : (expanded ? 'Show less' : 'Show more');
    };
    button.addEventListener('click', () => {
      const collapsing = expandedInputs.has(key);
      const offset = bubble.getBoundingClientRect().top - flow.getBoundingClientRect().top;
      const before = flow.scrollTop;
      const bubbleTop = before + offset;
      if (collapsing) expandedInputs.delete(key); else expandedInputs.add(key);
      update();
      
      
      flow.scrollTo({top: collapsing && offset < 0 ? Math.max(0, bubbleTop - 20) : before,
                     behavior:'instant'});
      button.focus({preventScroll:true});
    });
    bubble.after(button);
    update();
  });

  bindCopyButtons();
  renderOutline(s);
  const more = document.getElementById("moreMessages");
  if (more) {
    const loadMore = async () => {
      if (more.disabled) return;
      more.disabled = true;
      const seq = openSeq;
      state.msgLimit += 300;
      try {
        const fresh = await apiJson("api/records/session/" + encodeURIComponent(s.session_id)
          + "?limit=" + state.msgLimit);
        
        if (seq !== openSeq) return;
        state.current = fresh;
        renderMessages(fresh, true);
      } catch (err) {
        if (seq !== openSeq) return;
        
        
        state.msgLimit = Math.max(300, state.msgLimit - 300);
        more.disabled = false;
        toast(err.message || T("openFail"));
      }
    };
    more.onclick = loadMore;
    
    
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          io.disconnect();
          loadMore();
        }
      }, { root: flow, threshold: 0.1 });
      io.observe(more);
    }
  }
  if (preserveScroll && anchorIndex >= 0) {
    const items = anchorItems();
    const el = items[anchorIndex];
    if (el) {
      const delta = el.getBoundingClientRect().top - flow.getBoundingClientRect().top;
      flow.scrollTop += delta - anchorDelta;
    } else {
      flow.scrollTop = keepScroll;
    }
  } else {
    flow.scrollTop = keepScroll;
  }
  requestAnimationFrame(renderTurnRail);
}

function turnMarks() {
  const flow = document.getElementById("flow");
  return [...flow.querySelectorAll(
    ".msg.user, .msg.ai .body > p, .msg.ai .body > h1, .msg.ai .body > h2,"
    + " .msg.ai .body > h3, .msg.ai .body > h4, .msg.ai .body > .row,"
    + " .msg.ai .body > .turnproc-toggle,"
    + " .msg.ai .body > .codeblock, .msg.ai .body > ul, .msg.ai .body > ol")];
}


let railTargets = [];
function updateTurnRail() {
  const nav = document.getElementById("turnrail");
  const flow = document.getElementById("flow");
  if (!nav || nav.classList.contains("hidden")) return;
  const marks = [...document.querySelectorAll("#turnmarks .turnmark")];
  if (!marks.length) return;
  const users = railTargets.length === marks.length ? railTargets : turnMarks();
  const y = flow.scrollTop + Math.max(80, flow.clientHeight * 0.28);
  const flowTop = flow.getBoundingClientRect().top;
  let active = 0;
  users.forEach((el, i) => {
    const top = el.getBoundingClientRect().top - flowTop + flow.scrollTop;
    if (top <= y) active = i;
  });
  marks.forEach((el, i) => el.classList.toggle("on", i === active));
}
function renderTurnRail() {
  const nav = document.getElementById("turnrail");
  const flow = document.getElementById("flow");
  const marks = document.getElementById("turnmarks");
  const users = turnMarks();
  if (!nav || !marks || !users.length || flow.classList.contains("hidden")
      || flow.scrollHeight <= flow.clientHeight + 24) {
    if (nav) nav.classList.add("hidden");
    railTargets = [];
    return;
  }
  railTargets = users;
  nav.classList.remove("hidden");
  const total = Math.max(1, flow.scrollHeight);
  marks.innerHTML = users.map((el, i) => {
    const top = el.getBoundingClientRect().top - flow.getBoundingClientRect().top + flow.scrollTop;
    const pct = Math.max(0, Math.min(100, (top / total) * 100));
    const text = (el.textContent || "").replace(/\s+/g, " ").slice(0, 90);
    return '<button class="turnmark" type="button" data-turn="' + i
      + '" style="top:' + pct.toFixed(3) + '%" title="' + esc(text) + '"></button>';
  }).join("");
  for (const el of marks.querySelectorAll(".turnmark")) {
    el.onclick = (e) => {
      e.stopPropagation();
      const target = users[Number(el.dataset.turn)];
      if (!target) return;
      const top = target.getBoundingClientRect().top - flow.getBoundingClientRect().top
        + flow.scrollTop - 16;
      flow.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    };
  }
  updateTurnRail();
}



let railFrame = false;
document.getElementById("flow").addEventListener("scroll", () => {
  if (railFrame) return;
  railFrame = true;
  requestAnimationFrame(() => { railFrame = false; updateTurnRail(); });
}, { passive: true });


function toggleDrawer(open) {
  const d = document.getElementById("drawer");
  const isOpen = open === undefined ? !d.classList.contains("open") : open;
  d.classList.toggle("open", isOpen);
  document.body.classList.toggle("drawer-open", isOpen);
  if (isOpen) renderDrawer();
}
function kv(k, v) {
  return '<div class="kv"><span class="k">' + esc(k) + '</span><span class="v" title="'
    + esc(v) + '">' + esc(v) + "</span></div>";
}
function renderDrawer() {
  const s = state.current;
  const box = document.getElementById("dcontent");
  if (!s) { box.innerHTML = ""; return; }
  const cov = s.coverage || {};
  const total = cov.records || s.records || 0;
  const stamped = cov.stamped || 0;
  
  
  const byTurns = Number(s.turns || 0) > 0 && s.turns_stamped != null;
  const pct = byTurns ? Math.round(100 * s.turns_stamped / s.turns)
    : (total ? Math.round(100 * stamped / total) : 0);
  const stamps = s.stamps || [];
  const issued = stamps.filter((x) => !x.pending);
  const providers = [];
  for (const st of issued) {
    const name = tsaLabel(st.provider);
    if (name && !providers.includes(name)) providers.push(name);
  }
  const detail = s.stamp_detail || {};
  const stateText = s.stamp === "stamped"
    ? (s.stamp_partial ? T("stampPartial") : T("stampOk"))
    : s.stamp === "queued" ? T("stampWait") : T("stampNone");
  const models = (s.model_labels && s.model_labels.length) ? s.model_labels : (s.models || []);
  const modelLine = models.join("、")
    + ((s.efforts && s.efforts.length) ? " · " + s.efforts.join(" / ") : "");
  const integrityKnown = !!(s.integrity && s.integrity.ok !== null
    && s.integrity.ok !== undefined);
  const ok = s.integrity && s.integrity.ok;

  let html = '<div class="sect">' + esc(T("basics")) + "</div>"
    + kv(T("agent"), agentLabel(s.agent)
         + (s.surface ? " · " + surfaceLabel(s.surface) : ""))
    + kv(state.lang === "zh" ? "子智能体范围" : "Subagent coverage",
         state.lang === "zh" ? "仅展示已识别并记录的关系；不代表完整采集，未显示不等于没有使用。"
         : "Only identified, recorded relationships are shown. Coverage is incomplete; absence does not mean none were used.")
    + kv(T("started"), fmtDateTime(s.started_at))
    + kv(T("last"), fmtDateTime(s.last_at))
    + (s.turns ? kv(T("turns2"), String(s.turns)) : "")
    + (modelLine ? kv(T("models"), modelLine) : "");

  const committedTo = s.committed_to || 0;
  const commitText = !committedTo ? "—"
    : [(s.turns && s.turns_committed != null
          ? fill(T("turnsOf"), { a: s.turns_committed, b: s.turns }) : ""),
       (s.committed_at ? fmtDateTime(s.committed_at) : "")]
      .filter(Boolean).join(" · ") || "✓";
  html += '<div class="sect">' + esc(T("state")) + "</div>"
    + '<div class="kv"><span class="k" title="' + esc(T("checkpointHint")) + '">'
    + esc(T("checkpoint")) + '</span><span class="v">' + esc(commitText) + "</span></div>"
    + kv(T("proofSection"), stateText + (providers.length ? " · " + providers.join("、") : ""))
    + kv(T("integrity"), !integrityKnown ? T("integrityPending")
         : (ok ? "✓ " : "✗ ") + T(ok ? "integrityState" : "integrityBadState"));
  if (stamped > 0) {
    html += '<div class="kv sub"><span class="k">' + esc(T("coverage")) + "</span>"
      + '<span class="v">' + esc(byTurns
          ? fill(T("turnsOf"), { a: s.turns_stamped, b: s.turns }) : pct + "%")
      + "</span></div>"
      + '<div class="bar"><i style="width:' + pct + '%"></i></div>';
  }
  if (detail.created_at && s.stamp !== "none") {
    html += kv(T("stampAt"), fmtDateTime(detail.created_at));
  }
  if (stamps.length > 1) {
    
    
    const ordered = stamps.slice().reverse();
    const currentIndex = ordered.findIndex((x) => !x.pending);
    html += '<div class="sect2">' + esc(T("proofHistory"))
      + '<span class="count">' + stamps.length + "</span></div>"
      + '<div class="stamplist" tabindex="0" role="list">';
    ordered.forEach((st, i) => {
      const current = i === currentIndex;
      const when = fmtDateTime(st.gen_time || st.created_at);
      const provider = tsaLabel(st.provider) || (st.pending ? T("proofPending") : "—");
      const upTo = st.turns != null ? fill(T("turnsN"), { n: st.turns }) : "";
      html += '<div class="stampline' + (current ? " cur" : "") + '" role="listitem" title="'
        + esc([provider, upTo, when].filter(Boolean).join(" · ")) + '">'
        + '<span class="p">' + esc(provider) + "</span>"
        + '<span class="c">' + esc(upTo) + "</span>"
        + '<span class="d">' + esc(when) + "</span>"
        + (current ? '<span class="now">' + esc(T("proofCurrent")) + "</span>" : "")
        + "</div>";
    });
    html += "</div>";
  }

  const fullPath = (s.folder || "").replace(state.home || "\u0000", "~");
  const cut = fullPath.lastIndexOf("/");
  const dirPath = cut >= 0 ? fullPath.slice(0, cut + 1) : "";
  const leaf = cut >= 0 ? fullPath.slice(cut + 1) : fullPath;
  html += '<div class="sect">' + esc(T("location")) + "</div>"
    + '<button class="pathbox" id="dx-reveal" title="' + esc(revealLabel()) + '">'
    + '<span class="row1"><span class="dir">' + esc(dirPath || "—") + "</span>"
    + '<span class="z">' + esc(s.size || "") + "</span></span>"
    + '<span class="name">' + esc(leaf) + "</span></button>"
    + '<div class="acts2">'
    + '<button class="btn pri" id="dx-export">' + esc(T("export")) + "</button>"
    + '<div class="row2">'
    + '<button class="btn" id="dx-copy">' + esc(T("copyCmd")) + "</button>"
    + "</div></div>"
    + '<button class="linkbtn danger" id="dx-del">' + esc(T("delete")) + "</button>";
  box.innerHTML = html;
  document.getElementById("dx-export").onclick = () => exportSession(s.session_id);
  document.getElementById("dx-del").onclick = () => askDelete(s.session_id);
  document.getElementById("dx-reveal").onclick = () => reveal(s.session_id);
  document.getElementById("dx-copy").onclick = () => {
    
    
    copyText(s.folder || "", true).then((ok) => toast(ok ? T("copyDone") : T("copyFail")));
  };
}
function copyText(text, silent) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(
      () => { if (!silent) toast(T("copyDone")); return true; },
      () => { if (!silent) toast(T("copyFail")); return false; });
  }
  if (!silent) toast(text);
  return Promise.resolve(false);
}
async function reveal(sessionId) {
  try {
    await api("api/records/session/" + encodeURIComponent(sessionId) + "/reveal",
              { method: "POST" });
    toast(T("revealDone"));
  } catch (e) { toast(e.message || T("revealDone")); }
}


function downloadUrl(url) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
function fill(text, vals) {
  return text.replace(/\{(\w+)\}/g, (m, k) => (k in vals ? String(vals[k]) : m));
}
async function exportSession(sessionId) { return exportSelection([sessionId]); }
function closeRowMenu() {
  const menu = document.getElementById("rowmenu");
  if (!menu) return;
  menu.hidden = true;
  menu.innerHTML = "";
  for (const el of document.querySelectorAll(".item.menu-open")) {
    el.classList.remove("menu-open");
  }
}

function openRowMenu(ev, sid) {
  ev.stopPropagation();
  const menu = document.getElementById("rowmenu");
  const row = state.sessions.find((x) => x.session_id === sid);
  const items = [];
  items.push(
    ["export", T("menuExport"), ""],
    ["copyid", T("menuCopyId"), ""],
    ["copypath", T("menuCopyPath"), ""],
    ["reveal", revealLabel(), ""],
    ["delete", T("menuDelete"), "danger"],
  );
  menu.innerHTML = items.map(([act, label, cls]) =>
    '<button class="mi' + (cls ? " " + cls : "") + '" data-act="' + act + '">'
    + esc(label) + "</button>").join("");
  menu.hidden = false;
  const owner = ev.currentTarget.closest && ev.currentTarget.closest(".item");
  if (owner) owner.classList.add("menu-open");
  const rect = ev.currentTarget.getBoundingClientRect();
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  let left = rect.right - width;
  let top = rect.bottom + 6;
  if (left < 8) left = 8;
  if (top + height > window.innerHeight - 8) top = rect.top - height - 6;
  menu.style.left = Math.round(left) + "px";
  menu.style.top = Math.round(top) + "px";
  for (const btn of menu.querySelectorAll(".mi")) {
    btn.onclick = (e) => {
      e.stopPropagation();
      const act = btn.dataset.act;
      closeRowMenu();
      if (act === "export") exportSession(sid);
      else if (act === "copyid") { copyText(sid); toast(T("copiedId")); }
      else if (act === "copypath") {
        if (row && row.folder) copyText(row.folder);
        else toast(T("copyFail"));
      }
      else if (act === "reveal") reveal(sid);
      else if (act === "delete") askDelete(sid);
    };
  }
}

function modal(html, wiring) {
  const m = document.getElementById("modal");
  const c = document.getElementById("card");
  c.innerHTML = html;
  m.classList.add("open");
  if (wiring) wiring(c);
  const close = () => m.classList.remove("open");
  c.querySelectorAll("[data-close]").forEach((b) => (b.onclick = close));
  m.onclick = (e) => { if (e.target === m) close(); };
  return { close };
}
function askDelete(sessionId) {
  const s = state.sessions.find((x) => x.session_id === sessionId);
  if (!s) return;
  const dlg = modal(
    "<h3>" + esc(T("delTitle")) + "</h3>"
    + "<p><b>" + esc(sessionTitle(s)) + "</b></p>"
    + "<p>" + esc((s.turns ? s.turns + " " + T("turns") + " · " : "") + agentLabel(s.agent)) + "</p>"
    + "<p>" + esc(T("delBody")) + "</p>"
    + '<div class="row-actions">'
    + '<button class="btn" data-close>' + esc(T("delCancel")) + "</button>"
    + '<button class="btn danger" id="dogo">' + esc(T("delGo")) + "</button></div>",
    (c) => {
      c.querySelector("#dogo").onclick = async () => {
        c.querySelector("#dogo").disabled = true;
        try {
          await api("api/records/session/" + encodeURIComponent(sessionId),
                    { method: "DELETE" });
          dlg.close();
          toast(T("deleted"));
          await loadSessions();
          if (state.current && state.current.session_id === sessionId) {
            state.current = null;
            history.replaceState(null, "", "");   
            emptyState(state.sessions.length ? "pick" : "none");
          }
        } catch (e) {
          dlg.close();
          toast(T("delFail") + " " + (e.message || ""));
        }
      };
    });
}


async function loadSessions() {
  const data = await apiJson("api/records/sessions");
  state.home = data.home || "";
  state.platform = data.platform || "";
  state.apps = data.apps || {};
  state.sessions = data.sessions || [];
  renderAgents(); renderList();
}



let openSeq = 0;
async function openSession(sessionId) {
  const seq = ++openSeq;
  try {
    const visible = visibleSessions();
    const index = visible.findIndex((x) => x.session_id === sessionId);
    if (index >= state.shown) {
      state.shown = Math.ceil((index + 1) / PAGE_SIZE) * PAGE_SIZE;
      renderList(false);
    }
    
    
    state.msgLimit = 300;
    const s = await apiJson("api/records/session/" + encodeURIComponent(sessionId)
      + "?limit=" + state.msgLimit);
    if (seq !== openSeq) return;
    state.current = s;
    showSessionHead(s);
    renderMessages(s);
    renderDrawer();
    renderList();
  } catch (e) {
    if (seq !== openSeq) return;
    
    
    
    if (e.status === 404) {
      
      
      history.replaceState(null, "", "");
      try { await loadSessions(); } catch (e2) {  }
      state.current = null;
      emptyState(state.sessions.length ? "pick" : "none");
      return;
    }
    
    toast(e.message || T("openFail"));
    state.current = null;
    emptyState(state.sessions.length ? "pick" : "none");
  }
}


const LANGS = [
  { code: "zh", name: "简体中文", en: "Chinese" },
  { code: "en", name: "English", en: "" },
];

function revealLabel() {
  const p = state.platform || "";
  if (p === "darwin") return T("revealFinder");
  if (p === "win32") return T("revealExplorer");
  return T("revealFiles");
}

function langName(code) {
  const found = LANGS.find((l) => l.code === code);
  return found ? found.name : String(code || "").toUpperCase();
}

function renderLangMenu() {
  const menu = document.getElementById("langmenu");
  if (!menu) return;
  menu.innerHTML = LANGS.map((l) => {
    const on = l.code === state.lang;
    return '<button class="lang-item' + (on ? " on" : "") + '" role="option" '
      + 'aria-selected="' + on + '" data-code="' + esc(l.code) + '">'
      + '<span class="names"><span class="n">' + esc(l.name) + "</span>"
      + (l.en ? '<span class="e">' + esc(l.en) + "</span>" : "")
      + '</span><svg class="ck" viewBox="0 0 16 16"><path d="M3.5 8.6l3 3 6-6.2"/></svg>'
      + "</button>";
  }).join("");
  for (const el of menu.querySelectorAll(".lang-item")) {
    el.onclick = (e) => { e.stopPropagation(); applyLang(el.dataset.code); };
  }
}

function toggleLangMenu(open) {
  const menu = document.getElementById("langmenu");
  const btn = document.getElementById("lang");
  if (!menu || !btn) return;
  const isOpen = open === undefined ? !menu.classList.contains("open") : open;
  menu.classList.toggle("open", isOpen);
  btn.setAttribute("aria-expanded", String(isOpen));
}

function applyStaticTexts() {
  document.documentElement.lang = state.lang === "zh" ? "zh-CN" : "en";
  for (const el of document.querySelectorAll("[data-t]")) el.textContent = T(el.dataset.t);
  for (const el of document.querySelectorAll("[data-t-ph]")) el.placeholder = T(el.dataset.tPh);
  for (const el of document.querySelectorAll("[data-t-title]")) el.title = T(el.dataset.tTitle);
  document.title = T("brandTitle");
  const cur = document.querySelector("#lang .curr-lang");
  if (cur) cur.textContent = langName(state.lang);
  const langBtn = document.getElementById("lang");
  if (langBtn) langBtn.title = T("switchLang");
  renderLangMenu();
  applyTheme();
}

function applyLang(lang) {
  toggleLangMenu(false);
  state.lang = lang;
  try { localStorage.setItem("provelog-lang", lang); } catch (e) {}
  applyStaticTexts();
  renderAgents();
  renderList();
  if (state.current) {
    showSessionHead(state.current);
    renderMessages(state.current);
    if (document.getElementById("drawer").classList.contains("open")) renderDrawer();
  } else {
    emptyState(state.sessions.length ? "pick" : "none");
  }
}

function tickUtc() {
  const el = document.getElementById("utc");
  if (!el) return;
  const now = new Date();
  const p = (n) => String(n).padStart(2, "0");
  el.textContent = "UTC " + p(now.getUTCHours()) + ":" + p(now.getUTCMinutes())
    + ":" + p(now.getUTCSeconds());
  const full = now.toISOString().slice(0, 19).replace("T", " ") + " UTC";
  el.title = full;
  el.setAttribute("aria-label", full);
}

async function boot() {
  state.lang = detectLang();
  applyStaticTexts();
  tickUtc();
  document.addEventListener("click", (e) => {
    if (!e.target.matches(".title-toggle")) return;
    const expanded = e.target.parentElement.classList.toggle("expanded");
    e.target.textContent = state.lang === "zh" ? (expanded ? "收起标题" : "展开标题") : (expanded ? "Show less" : "Show more");
  });
  const selectionObserver=new MutationObserver(changes=>{if(changes.some(c=>[...c.addedNodes].some(n=>n.nodeType===1&&!n.classList.contains('selection-box'))))drawSelection();});
  selectionObserver.observe(document.getElementById('agents'),{childList:true});
  selectionObserver.observe(document.getElementById('list'),{childList:true});
  drawSelection();
  setInterval(tickUtc, 1000);

  document.getElementById("lang").onclick = (e) => {
    e.stopPropagation();
    toggleLangMenu();
  };
  document.addEventListener("click", (e) => {
    const proc = e.target.closest && e.target.closest("[data-proc-toggle]");
    if (proc) {
      const key = proc.dataset.procToggle;
      const open = proc.getAttribute("aria-expanded") !== "true";
      proc.setAttribute("aria-expanded", open ? "true" : "false");
      proc.classList.toggle("open", open);
      for (const el of document.querySelectorAll("[data-proc-body]")) {
        if (el.dataset.procBody === key) el.hidden = !open;
      }
      return;
    }
    const shot = e.target.closest && e.target.closest("a.shotlink");
    if (shot) {
      e.preventDefault();
      const img = shot.querySelector("img");
      openLightbox(shot.href, (img && img.alt) || shot.title || "");
      return;
    }
    const sw = document.querySelector(".lang-switcher");
    if (sw && !sw.contains(e.target)) toggleLangMenu(false);
    const menu = document.getElementById("rowmenu");
    if (menu && !menu.hidden && !menu.contains(e.target)) closeRowMenu();
    const kids = document.getElementById("kidmenu");
    const kidBtn = document.getElementById("children");
    const sibBtn = document.getElementById("siblings");
    if (kids && !kids.classList.contains("hidden")
        && !kids.contains(e.target)
        && !(kidBtn && kidBtn.contains(e.target))
        && !(sibBtn && sibBtn.contains(e.target))) {
      closeKidMenu();
    }
  });
  document.addEventListener("error", (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || !img.dataset.fallback) return;
    const miss = document.createElement("span");
    miss.className = "imgmiss";
    miss.textContent = img.dataset.fallback;
    const host = img.closest("a.shotlink") || img;
    host.replaceWith(miss);
  }, true);
  const lightbox = document.getElementById("lightbox");
  lightbox.querySelector(".lbmask").onmousedown = closeLightbox;
  lightbox.querySelector(".lbclose").onclick = closeLightbox;
  const listEl = document.getElementById("list");
  listEl.addEventListener("scroll", () => {
    closeRowMenu();
    if (!listNearBottom(listEl)) return;
    const rows = visibleSessions();
    if (rows.length > state.shown) {
      state.shown += PAGE_SIZE;
      renderList(false);
    }
  });
  for (const el of document.querySelectorAll("#viewtabs button")) {
    el.onclick = () => setView(el.dataset.view);
  }
  document.getElementById("theme").onclick = toggleTheme;
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => { if (!themePref()) applyTheme(); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  document.getElementById("searchbtn").onclick = () => toggleSearch();
  document.getElementById("searchclose").onclick = () => toggleSearch(false);
  const sortBtn = document.getElementById("sortbtn");
  sortBtn.onclick = () => {
    state.sort = state.sort === "newest" ? "oldest" : "newest";
    sortBtn.title = T(state.sort === "newest" ? "sortNewest" : "sortOldest");
    sortBtn.setAttribute("aria-label", sortBtn.title);
    renderList();
  };
  document.getElementById("q").oninput = (e) => {
    state.q = e.target.value; renderList();
  };

  document.getElementById("closeDrawer").onclick = () => toggleDrawer(false);
  document.addEventListener("keydown", (e) => {
    const lb = document.getElementById("lightbox");
    if (lb && !lb.hidden) {
      if (e.key === "Escape") {
        e.preventDefault(); e.stopPropagation(); closeLightbox(); return;
      }
      if (e.key === "Tab") {
        e.preventDefault(); lb.querySelector(".lbclose").focus(); return;
      }
    }
    if (e.key === "Escape") {
      const q = document.getElementById("q");
      if (document.activeElement === q && q.value) {
        q.value = ""; state.q = ""; renderList();
      } else if (document.activeElement === q
                 || !document.getElementById("searchpanel").classList.contains("hidden")) {
        toggleSearch(false);
      }
      closeRowMenu();
      toggleLangMenu(false);
      toggleDrawer(false);
      document.getElementById("modal").classList.remove("open");
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      toggleSearch(true);
    }
  });
  document.getElementById("flow").addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-evopen]");
    if (!b || !state.current) return;
    const sec = b.closest(".turnsec");
    const info = sec ? sec.querySelector(":scope > .tinfo") : null;
    if (!info) return;
    info.hidden = !info.hidden;
    sec.classList.toggle("ev-open", !info.hidden);
    if (!info.hidden) info.innerHTML = turnInfoHTML(state.current, Number(sec.dataset.turn));
  });
  document.getElementById("outline").addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[data-turn]");
    const sec = a ? document.getElementById("turn-" + a.dataset.turn) : null;
    if (!sec) return;
    
    
    const flow = document.getElementById("flow");
    flow.style.scrollBehavior = "auto";
    flow.scrollTop += sec.getBoundingClientRect().top - flow.getBoundingClientRect().top - 12;
    flow.style.scrollBehavior = "";
  });
  window.addEventListener("hashchange", () => {
    closeLightbox();
    const id = decodeURIComponent(location.hash.slice(1));
    if (id) openSession(id); else { openSeq++; state.current = null; toggleDrawer(false); emptyState(state.sessions.length ? "pick" : "none"); }
  });

  await loadSessions();
  
  
  
  
  
  const signature = (s) => s ? JSON.stringify([
    s.title || "", s.title_hint || "", s.untitled || false,
    (s.messages || []).length,
    s.stats || null, s.coverage || null, s.anchors || null,
    
    
    
    s.stamp || "", s.stamp_partial || false, s.stamps || null,
    s.host_turns || null, s.capture_tier || "unknown",
    s.head_omitted || false]) : "";
  
  
  let refreshing = false;
  let lastBigRead = 0;
  const softRefresh = async () => {
    if (document.hidden) return;
    if (document.querySelector("#modal.open")) return;
    if (refreshing) return;
    refreshing = true;
    let openId = "";
    try {
      const data = await apiJson("api/records/sessions");
      const listChanged = JSON.stringify(data.sessions || []) !== JSON.stringify(state.sessions);
      openId = state.current ? state.current.session_id : "";
      const prevRow = openId ? state.sessions.find((x) => x.session_id === openId) : null;
      state.sessions = data.sessions || [];
      if (listChanged) { renderAgents(); renderList(); }
      if (!state.current) return;
      
      
      
      
      if (Number(state.current.records || 0) > 2000) {
        const row = state.sessions.find((x) => x.session_id === state.current.session_id);
        if (row) {
          state.current.records = row.records;
          state.current.last_at = row.last_at;
          state.current.turns = row.turns;
          state.current.stamp = row.stamp;
          if (!state.current.title && row.title) state.current.title = row.title;
          showSessionHead(state.current);
        }
        
        
        
        const changed = row && row.rev && state.current.rev
          && row.rev !== state.current.rev;
        if (!changed || Date.now() - lastBigRead < 30000) return;
        lastBigRead = Date.now();
      }
      const id = state.current.session_id;
      
      
      
      
      const row = state.sessions.find((x) => x.session_id === id);
      if (row && row.rev && row.rev === state.current.rev && prevRow
          && JSON.stringify(prevRow) === JSON.stringify(row)) return;
      const seq = openSeq;
      const limit = state.msgLimit;
      const fresh = await apiJson("api/records/session/" + encodeURIComponent(id)
        + "?limit=" + limit);
      
      
      
      if (seq !== openSeq || !state.current || state.current.session_id !== id
          || state.msgLimit !== limit) return;
      if (signature(fresh) === signature(state.current)) {
        
        
        state.current.rev = fresh.rev;
        return;
      }
      const flow = document.getElementById("flow");
      const keep = flow ? flow.scrollTop : 0;
      state.current = fresh;
      showSessionHead(fresh); renderMessages(fresh); renderDrawer();
      if (flow) flow.scrollTop = keep;
      renderList();
    } catch (e) {
      
      
      if (e.status === 404 && openId && state.current
          && state.current.session_id === openId) await openSession(openId);
      
    } finally {
      refreshing = false;
    }
  };
  
  
  
  setInterval(softRefresh, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) softRefresh(); });
  window.addEventListener("focus", softRefresh);
  
  
  
  window.addEventListener("pagehide", () => {
    try {
      const base = location.pathname.endsWith("/")
        ? location.pathname : location.pathname + "/";
      navigator.sendBeacon(base + "__provelog/bye");
    } catch (e) {  }
  });

  
  
  
  
  const id = decodeURIComponent(location.hash.slice(1));
  if (id) {
    
    
    
    openSession(id);
  } else if (state.sessions.length) {
    
    
    const first = state.sessions.find((s) => Number(s.turns || 0) > 0
                                         || Number(s.records || 0) > 10);
    if (first) openSession(first.session_id);
    else emptyState("pick");
  } else {
    emptyState("none");
  }
}
boot();
</script>
</body>
</html>
"""

PAGE_HTML = PAGE_HTML.replace(
    "__AGENT_ICONS__", json.dumps(AGENT_ICONS, separators=(",", ":")))
