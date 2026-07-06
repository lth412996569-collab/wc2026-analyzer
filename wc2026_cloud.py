# -*- coding: utf-8 -*-
"""竞彩足球 AI 分析师 - 云部署版 (Render.com)"""
import sys, os, re, json, math, time
import requests
from datetime import datetime
from collections import defaultdict
from flask import Flask, jsonify, request

if sys.platform == 'win32':
    try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except: pass

# ========== 模型 ==========
def poisson_pmf(k, lam):
    if lam <= 0: return 1.0 if k == 0 else 0.0
    return (lam**k)*math.exp(-lam)/math.factorial(k)

def score_matrix(hl, al, mg=5):
    return {f"{h}-{a}": round(poisson_pmf(h,hl)*poisson_pmf(a,al)*100,2) for h in range(mg+1) for a in range(mg+1)}

def outcome_probs(hl, al, mg=5):
    hw=dw=aw=0.0
    for h in range(mg+1):
        for a in range(mg+1):
            p=poisson_pmf(h,hl)*poisson_pmf(a,al)
            if h>a: hw+=p
            elif h==a: dw+=p
            else: aw+=p
    t=hw+dw+aw
    return {"home":round(hw/t*100,1),"draw":round(dw/t*100,1),"away":round(aw/t*100,1)}

def elo_prob(ra, rb):
    ea=1/(1+10**((rb-ra)/400))
    dp=max(0.15,0.35-abs(ea-0.5)*0.4)
    return round(ea*(1-dp)*100,1),round(dp*100,1),round(eb:=1-ea,1),eb

def factor_score(profile, is_home, adj=None):
    adj=adj or {}
    w={"attack":0.15,"defense":0.15,"form":0.12,"h2h":0.08,"home":0.08,"key":0.08,"exp":0.06,"style":0.07,"rest":0.05,"ref":0.05,"weather":0.05,"momentum":0.06}
    f={"attack":min(profile.get("goals_pg",1.5)/3*10+adj.get("attack",0),9.5),
       "defense":min(max(10-profile.get("conc_pg",1.5)*3+adj.get("defense",0),0.5),9.5),
       "form":min(max(profile.get("form",5)+adj.get("form",0),0.5),9.5),
       "h2h":min(max(profile.get("h2h",5)+adj.get("h2h",0),0.5),9.5),
       "home":(8 if is_home else 4)+adj.get("home",0),
       "key":min(max(profile.get("key",5)+adj.get("key",0),0.5),9.5),
       "exp":min(max(profile.get("exp",5)+adj.get("exp",0),0.5),9.5),
       "style":min(max(profile.get("style_adv",5)+adj.get("style",0),0.5),9.5),
       "rest":min(max(profile.get("rest",4)/7*10+adj.get("rest",0),0.5),9.5),
       "ref":min(max(profile.get("ref",5)+adj.get("ref",0),0.5),9.5),
       "weather":min(max(profile.get("venue",5)+adj.get("weather",0),0.5),9.5),
       "momentum":min(max(profile.get("momentum",5)+adj.get("momentum",0),0.5),9.5)}
    return round(sum(max(0.5,f[k])*v for k,v in w.items()),1)

def estimate_lambda(avg_scored, avg_conceded, opp_def, is_home, ha=0.3):
    base=(avg_scored+opp_def)/2
    if is_home: base+=ha
    return max(base,0.1)

# ========== 球队DB ==========
TEAM_DB = {
    "葡萄牙":{"elo":1780,"form":7,"goals_pg":2.0,"conc_pg":0.5,"h2h":4,"key":8,"exp":7,"style_adv":5,"rest":4,"ref":6,"venue":5,"momentum":7},
    "西班牙":{"elo":1850,"form":8.5,"goals_pg":2.0,"conc_pg":0,"h2h":6,"key":8.5,"exp":8,"style_adv":5,"rest":4,"ref":4,"venue":5,"momentum":8.5},
    "美国":{"elo":1680,"form":7.5,"goals_pg":2.5,"conc_pg":1.0,"h2h":3,"key":7.5,"exp":5,"style_adv":5,"rest":5,"ref":4,"venue":9,"momentum":7.5},
    "比利时":{"elo":1750,"form":7,"goals_pg":2.25,"conc_pg":1.0,"h2h":7,"key":8,"exp":9,"style_adv":5,"rest":5,"ref":5,"venue":2,"momentum":7},
    "阿根廷":{"elo":1900,"form":9,"goals_pg":2.5,"conc_pg":0.5,"h2h":8,"key":9.5,"exp":9,"style_adv":7,"rest":5,"ref":5,"venue":5,"momentum":9},
    "埃及":{"elo":1550,"form":6,"goals_pg":0.7,"conc_pg":0.3,"h2h":2,"key":6.5,"exp":3,"style_adv":3,"rest":5,"ref":5,"venue":5,"momentum":5},
    "瑞士":{"elo":1640,"form":6.5,"goals_pg":1.3,"conc_pg":1.0,"h2h":5,"key":5.5,"exp":6,"style_adv":5,"rest":4,"ref":5,"venue":5,"momentum":6},
    "哥伦比亚":{"elo":1670,"form":7,"goals_pg":1.5,"conc_pg":0.5,"h2h":5,"key":7,"exp":6,"style_adv":5,"rest":4,"ref":5,"venue":5,"momentum":6.5},
}

# ========== 深度分析 ==========
MATCH_ANALYSIS_DB = {
    "葡萄牙_西班牙": {
        "title":"伊比利亚德比——C罗第六次世界杯的宿命之战",
        "referee":{"name":"安东尼·泰勒","nationality":"英格兰","style":"先松后紧，鼓励身体对抗","detail":"前60分钟容忍身体接触，后30分钟突然收紧。英超场均4.15黄，世界杯仅2.33黄。对禁区灰色接触不判，战术犯规果断给牌。","impact":"利好葡萄牙防守硬度+利好西班牙传控节奏"},
        "venue":{"name":"达拉斯AT&T体育场","detail":"封闭穹顶22°C恒温，天然混合草坪。80,000观众声浪在封闭环境下干扰场上沟通。"},
        "h2h":"历史41场:西班牙18胜16平7负。2018年C罗帽子戏法3-3；2025年欧国联决赛葡萄牙点球夺冠。",
        "tactics":{"home":{"name":"葡萄牙","formation":"4-3-3防守反击","plan":"上半场龟缩，下半场莱奥速度反击+C罗定位球终结。取胜依赖C罗支点+莱奥/拉莫斯边路反击。","key":"C罗(3球)、莱奥、拉莫斯"},"away":{"name":"西班牙","formation":"4-3-3控球压迫","plan":"65%+控球，罗德里+佩德里+加维中场绞杀。库库雷利亚→奥亚萨瓦尔左路连线是固定得分套路。4场零封。","key":"奥亚萨瓦尔(4球)、亚马尔、罗德里"}},
        "goal_patterns":{"home":"下半场多(62.5%):开场闪击+绝杀","away":"上半场多(62.5%):16-45分钟黄金窗口"},
        "x_factor":"泰勒'钓鱼执法'模式——转折点何时到来是最大变量。一旦50分钟出现大规模冲突，接下来30分钟变'牌王'。"},
    "美国_比利时": {
        "title":"2014重演——美国主场复仇还是比利时再续绝杀神话",
        "referee":{"name":"阿德汉姆·马哈德梅","nationality":"约旦","style":"严格纪律，对鲁莽犯规零容忍","detail":"亚冠半决赛单场11黄+1红。本届场均2黄，对点球申诉保守。英格兰战评8/10分。","impact":"美国高压逼抢面临吃牌风险；比利时技术流受益"},
        "venue":{"name":"流明球场","detail":"露天21°C，天然草。美国队堡垒(西雅图8连胜)。6.8万观众声浪直接压向球场。"},
        "h2h":"2014世界杯1/8决赛:比利时2-1美国(加时，霍华德16次扑救)。12年后重演。",
        "tactics":{"home":{"name":"美国","formation":"4-3-3高压转换","plan":"开场闪电战+31-45分钟高压冲击。巴洛贡(3球)红牌被FIFA取消，士气巅峰。普利西奇伤愈回归。","key":"巴洛贡(3球)、普利西奇、蒂尔曼"},"away":{"name":"比利时","formation":"4-2-3-1慢热+末段爆发","plan":"上半场消耗，65分钟后发力。86分钟后打入3球。对塞内加尔0-2到85分钟最终3-2加时逆转。","key":"德布劳内、卢卡库(2球)、蒂勒曼斯(3球)"}},
        "goal_patterns":{"home":"上半场多(60%):31-45分钟4球，开场闪击","away":"下半场多(67%):86分钟后3球全是救命球"},
        "x_factor":"巴洛贡红牌复活——FIFA 7月5日取消停赛。美国队48小时内从失去核心到核心归位，士气推到顶峰。"},
    "阿根廷_埃及": {
        "title":"梅西的世界杯谢幕——阿根廷碾压还是埃及铁桶？",
        "referee":{"name":"待公布","nationality":"待定","style":"淘汰赛尺度偏紧","detail":"FIFA通常指派经验丰富裁判。淘汰赛保护进攻球员倾向明显。","impact":"利好阿根廷技术流；埃及防守硬度受限"},
        "venue":{"name":"待公布","detail":"详见FIFA官方"},
        "h2h":"无交锋记录。阿根廷对非洲球队近5场4胜1平。",
        "tactics":{"home":{"name":"阿根廷","formation":"4-3-3梅西核心","plan":"控球率65%+，梅西中路突破+阿尔瓦雷斯跑位。防守端4场仅丢1球。埃及大概率摆大巴，需要耐心。","key":"梅西、阿尔瓦雷斯、恩佐"},"away":{"name":"埃及","formation":"5-4-1深度防守","plan":"历史性闯入淘汰赛，依赖萨拉赫反击+全员防守。小组赛3场仅进2球但只丢1球。","key":"萨拉赫、埃尔内尼"}},
        "goal_patterns":{"home":"上半场65%，场均2.5球","away":"场均0.7球，75%来自反击"},
        "x_factor":"埃及门将希纳维是扑救王之一。阿根廷心态波动风险——梅西最后一次世界杯压力是双刃剑。"},
    "瑞士_哥伦比亚": {
        "title":"欧洲铁壁vs南美黑马——谁的黑马成色更足？",
        "referee":{"name":"待公布","nationality":"待定","style":"中立场执法","detail":"双方都偏防守反击，身体对抗多。裁判犯规尺度直接影响比赛节奏。","impact":"偏松利好瑞士；偏紧利好哥伦比亚"},
        "venue":{"name":"待公布","detail":"详见FIFA官方"},
        "h2h":"世界杯2次:哥伦比亚1胜1平。最近2018年哥伦比亚2-0瑞士。",
        "tactics":{"home":{"name":"瑞士","formation":"4-2-3-1防守反击","plan":"扎卡+弗罗伊勒双后腰屏障。进攻依赖定位球+恩博洛支点。","key":"扎卡、恩博洛、索默"},"away":{"name":"哥伦比亚","formation":"4-3-3快速转换","plan":"路易斯·迪亚斯边路突破是最大武器。4场仅丢1球。擅长利用对手失误反击。","key":"迪亚斯、J罗、米纳(定位球)"}},
        "goal_patterns":{"home":"场均1.3球，60%下半场","away":"场均1.5球，近半来自定位球"},
        "x_factor":"哥伦比亚2014年曾闯八强，淘汰赛经验优。瑞士三届均止步1/8——十六郎魔咒？"},
}

def get_match_analysis(home, away):
    for k in [f"{home}_{away}", f"{away}_{home}"]:
        if k in MATCH_ANALYSIS_DB:
            data = MATCH_ANALYSIS_DB[k].copy()
            if k == f"{away}_{home}" and "tactics" in data:
                data["tactics"]["home"], data["tactics"]["away"] = data["tactics"]["away"], data["tactics"]["home"]
            if k == f"{away}_{home}" and "goal_patterns" in data:
                data["goal_patterns"]["home"], data["goal_patterns"]["away"] = data["goal_patterns"]["away"], data["goal_patterns"]["home"]
            return data
    hp=TEAM_DB.get(home,{"elo":1500,"goals_pg":1.5,"conc_pg":1.5,"momentum":5})
    ap=TEAM_DB.get(away,{"elo":1500,"goals_pg":1.5,"conc_pg":1.5,"momentum":5})
    gap=hp["elo"]-ap["elo"]
    fav=home if gap>0 else away
    return {"title":f"{home} vs {away}","tactics":{"home":{"name":home,"plan":f"Elo:{hp['elo']},场均{hp['goals_pg']}球/失{hp['conc_pg']}球。{fav if gap>30 else '实力接近'}。","key":"暂无"},"away":{"name":away,"plan":f"Elo:{ap['elo']},场均{ap['goals_pg']}球/失{ap['conc_pg']}球。","key":"暂无"}},"goal_patterns":{"home":f"场均{hp['goals_pg']}球","away":f"场均{ap['goals_pg']}球"},"h2h":"暂无交锋数据"}

# ========== 赔率抓取 ==========
def fetch_odds():
    try:
        r=requests.get('https://trade.500.com/jczq/',headers={'User-Agent':'Mozilla/5.0'},timeout=15)
        r.encoding='gb2312'
    except: return []
    matches=[]
    for m in re.finditer(r'data-homesxname="([^"]*)"(.*?)</tr>',r.text,re.DOTALL):
        home=m.group(1);row=m.group(0)
        def a(p):x=re.search(p,row);return x.group(1).strip() if x else ""
        away=a(r'data-awaysxname="([^"]*)"')
        if not away: continue
        ranks=re.findall(r'\[(\d+)\]',row)
        nums=[float(x) for x in re.findall(r'(\d+\.\d{2})',re.sub(r'<[^>]+>','|',row)) if 1<float(x)<999]
        if len(nums)<6: continue
        matches.append({"id":a(r'data-matchnum="([^"]*)"'),"league":a(r'data-simpleleague="([^"]*)"'),
            "time":f"{a(r'data-matchdate=([^"]*)')} {a(r'data-matchtime=([^"]*)')}",
            "home":home,"away":away,"hr":ranks[0]if len(ranks)>=2 else"","ar":ranks[1]if len(ranks)>=2 else"",
            "handicap":a(r'data-rangqiu="([^"]*)"'),"spf_w":nums[0],"spf_d":nums[1],"spf_l":nums[2],
            "rq_w":nums[3],"rq_d":nums[4],"rq_l":nums[5]})
    return matches

# ========== 分析 ==========
def analyze(match):
    h,a=match["home"],match["away"]
    hp=TEAM_DB.get(h,{"elo":1500,"form":5,"goals_pg":1.5,"conc_pg":1.5,"h2h":5,"key":5,"exp":5,"style_adv":5,"rest":4,"ref":5,"venue":5,"momentum":5})
    ap=TEAM_DB.get(a,{"elo":1500,"form":5,"goals_pg":1.5,"conc_pg":1.5,"h2h":5,"key":5,"exp":5,"style_adv":5,"rest":4,"ref":5,"venue":5,"momentum":5})
    t=1/match["spf_w"]+1/match["spf_d"]+1/match["spf_l"]
    op={"home":round(1/match["spf_w"]/t*100,1),"draw":round(1/match["spf_d"]/t*100,1),"away":round(1/match["spf_l"]/t*100,1)}
    eh,ed,ea,_=elo_prob(hp["elo"],ap["elo"])
    ep={"home":eh,"draw":ed,"away":ea}
    hs=factor_score(hp,True);aws=factor_score(ap,False)
    diff=hs-aws;dp=max(18,32-abs(diff)*0.6);hw=max(5,50+diff*2-dp/2);aw=max(5,100-hw-dp)
    fp={"home":round(hw,1),"draw":round(dp,1),"away":round(aw,1)}
    mp={"home":round(op["home"]*.15+ep["home"]*.20+fp["home"]*.65,1),"draw":round(op["draw"]*.15+ep["draw"]*.20+fp["draw"]*.65,1),"away":round(op["away"]*.15+ep["away"]*.20+fp["away"]*.65,1)}
    hl=estimate_lambda(hp["goals_pg"],hp["conc_pg"],ap["conc_pg"],True)
    al=estimate_lambda(ap["goals_pg"],ap["conc_pg"],hp["conc_pg"],False)
    scores=sorted(score_matrix(hl,al).items(),key=lambda x:x[1],reverse=True)[:8]
    po=outcome_probs(hl,al)
    vb=[{"label":{"home":h+"胜","draw":"平局","away":a+"胜"}[k],"odds":odds,"mi":round(1/odds*100,1),"mp":mp[k],"edge":round((mp[k]/100-1/odds)*100,1)} for k,odds in [("home",match["spf_w"]),("draw",match["spf_d"]),("away",match["spf_l"])] if mp[k]/100-1/odds>0.02]
    best=max(mp,key=mp.get);labels={"home":h+"胜","draw":"平局","away":a+"胜"}
    stars="⭐⭐⭐⭐⭐" if mp[best]>=75 else ("⭐⭐⭐⭐" if mp[best]>=50 else ("⭐⭐⭐" if mp[best]>=35 else "⭐⭐"))
    recs=[f"{stars} 首选:{labels[best]}({mp[best]}%)"]
    for v in vb[:3]:recs.append(f"💰 价值:{v['label']} 赔率{v['odds']:.2f} 概率{v['mp']}% 优势{v['edge']}%")
    sp=sum(p for s,p in score_matrix(hl,al,5).items() if sum(map(int,s.split('-')))>2)
    ap_all=sum(p for _,p in score_matrix(hl,al,5).items())
    over_r=sp/ap_all if ap_all>0 else 0.5
    recs.append(f"⚽ 总进球:{'大球' if over_r>0.45 else '小球'}({'大' if over_r>0.45 else '小'}球概率{over_r:.0%})")
    btts_p=sum(p for s,p in score_matrix(hl,al,5).items() if int(s[0])>0 and int(s[2])>0)/ap_all
    recs.append(f"🥅 双方进球:{'是' if btts_p>0.4 else '否'}(概率{btts_p:.0%})")
    return {"poisson":{"hl":round(hl,2),"al":round(al,2),"scores":scores,"outcome":po},"odds_probs":op,"elo_probs":ep,"factor_probs":fp,"model_probs":mp,"factor_score":{"home":hs,"away":aws},"value_bets":vb,"recs":recs,"btts":round(btts_p*100,1),"over":round(over_r*100,1),"signals":[],"news_headlines":[],"adjustments":{"home":{},"away":{}},"odds_movement":{}}

# ========== Flask ==========
app=Flask(__name__)

@app.route('/')
def index():
    return INDEX_HTML

@app.route('/api/matches')
def api_matches():
    matches=fetch_odds()
    results=[{"match":m,"model":a["model_probs"],"factor_score":a["factor_score"],"poisson":a["poisson"],"value_bets":a["value_bets"],"recs":a["recs"],"btts":a["btts"],"over":a["over"],"signals":a["signals"],"news_headlines":a["news_headlines"],"odds_movement":a["odds_movement"]} for m in matches for a in [analyze(m)]]
    return jsonify({"time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"count":len(results),"results":results})

@app.route('/api/match_detail')
def api_match_detail():
    home=request.args.get('home','');away=request.args.get('away','')
    if not home or not away: return jsonify({"error":"need home and away"}),400
    hp=TEAM_DB.get(home,{});ap=TEAM_DB.get(away,{})
    detail=get_match_analysis(home,away)
    hl=estimate_lambda(hp.get("goals_pg",1.5),hp.get("conc_pg",1.5),ap.get("conc_pg",1.5),True)
    al=estimate_lambda(ap.get("goals_pg",1.5),ap.get("conc_pg",1.5),hp.get("conc_pg",1.5),False)
    return jsonify({"home":home,"away":away,"home_profile":hp,"away_profile":ap,"detail":detail,"poisson_lambda":{"home":round(hl,2),"away":round(al,2)}})

# ========== HTML (精简版) ==========
INDEX_HTML = open(os.path.join(os.path.dirname(__file__), 'index_cloud.html'), 'r', encoding='utf-8').read() if os.path.exists(os.path.join(os.path.dirname(__file__), 'index_cloud.html')) else r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>竞彩足球 AI 分析师</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Microsoft YaHei',-apple-system,sans-serif;background:linear-gradient(135deg,#0a0f1a,#1a1f2e);color:#e0e0e0;min-height:100vh}
.header{background:linear-gradient(135deg,#1a3a5c,#0d1f3c);padding:15px 25px;display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #2a5a8a;position:sticky;top:0;z-index:100}
.header h1{font-size:1.3em;color:#4fc3f7}.btn{background:#4fc3f7;color:#0a0f1a;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:bold;font-size:0.85em}.btn:hover{background:#81d4fa}
.container{max-width:1100px;margin:0 auto;padding:20px}
.match-card{background:#151d2e;border-radius:14px;padding:22px;margin-bottom:18px;border:1px solid #2a3a5a;cursor:pointer;transition:0.3s}
.match-card:hover{border-color:#4fc3f7}.match-card.expanded{border-color:#ff9800}
.match-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.id{background:#4fc3f7;color:#0a0f1a;padding:4px 10px;border-radius:20px;font-weight:bold;font-size:0.8em}
.league{color:#78909c;font-size:0.8em}.time{color:#ffa726;font-size:0.85em}
.teams{display:flex;justify-content:center;align-items:center;gap:20px;margin:10px 0}
.team{text-align:center}.tn{font-size:1.2em;font-weight:bold;color:#fff}.tr{font-size:0.75em;color:#78909c}
.vs{font-size:1.4em;color:#ffa726;font-weight:bold}
.odds-g{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}
.odds-b{background:#0d1525;padding:8px;border-radius:8px;text-align:center}.odds-b .l{font-size:0.7em;color:#78909c}.odds-b .v{font-size:1.1em;color:#4fc3f7;font-weight:bold}
.prob-bar{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:12px 0}
.pb-h{background:#66bb6a}.pb-d{background:#ffa726}.pb-a{background:#ef5350}
.pl{display:flex;justify-content:space-between;font-size:0.8em;color:#78909c}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0}
.box{background:#0d1525;border-radius:10px;padding:12px}.box h4{color:#4fc3f7;font-size:0.85em;margin-bottom:6px}
.chips{display:flex;flex-wrap:wrap;gap:5px}.chip{background:#1a2d3d;padding:4px 10px;border-radius:12px;font-size:0.8em;color:#81d4fa}
.rec{background:#0d2a1a;border-left:3px solid #66bb6a;padding:7px 10px;margin:5px 0;border-radius:0 8px 8px 0;font-size:0.8em}
.val{background:#2a1a0d;border-left:3px solid #ff9800;padding:7px 10px;margin:5px 0;border-radius:0 8px 8px 0;font-size:0.8em}
.detail-panel{display:none;margin-top:15px;padding-top:15px;border-top:1px solid #2a3a5a}
.match-card.expanded .detail-panel{display:block}
.dsec{margin:10px 0}.dsec h4{color:#ff9800;font-size:0.85em;margin-bottom:6px}
.dg{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0}
.dc{background:#0d1525;border-radius:8px;padding:10px;font-size:0.78em}
.dc .dn{color:#4fc3f7;font-weight:bold;font-size:0.9em}.dc .dr{color:#78909c;font-size:0.75em}.dc .dc{color:#b0bec5;line-height:1.5;margin-top:4px}
.click-hint{text-align:center;color:#546e7a;font-size:0.7em;margin-top:6px}
.expand-icon{float:right;color:#546e7a;font-size:0.8em}.match-card.expanded .expand-icon{color:#ff9800;transform:rotate(180deg)}
.footer{text-align:center;padding:20px;color:#546e7a;font-size:0.75em}
.tabs{display:flex;gap:6px;margin-bottom:18px;flex-wrap:wrap}
.tab{padding:6px 14px;background:#151d2e;border:1px solid #2a3a5a;border-radius:20px;cursor:pointer;color:#78909c;font-size:0.8em;transition:0.3s}
.tab.active,.tab:hover{background:#4fc3f7;color:#0a0f1a;border-color:#4fc3f7}
.loading{text-align:center;padding:60px;color:#78909c;font-size:1.1em}
.spinner{display:inline-block;width:36px;height:36px;border:3px solid #2a3a5a;border-top:3px solid #4fc3f7;border-radius:50%;animation:spin 1s linear infinite;margin-bottom:12px}
@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:600px){.g2{grid-template-columns:1fr}.teams{flex-direction:column}.dg{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header"><div><h1>🏆 竞彩足球 AI 分析师</h1><div id="ut" style="font-size:0.8em;color:#90a4ae">加载中...</div></div><button class="btn" onclick="refresh()">🔄 刷新数据</button></div>
<div class="container">
<div class="tabs"><div class="tab active" onclick="filter('all')">全部</div><div class="tab" onclick="filter('世界杯')">🏆 世界杯</div><div class="tab" onclick="filter('瑞超')">🇸🇪 瑞超</div></div>
<div id="content"><div class="loading"><div class="spinner"></div><p>AI模型分析中...</p></div></div></div>
<div class="footer">泊松分布+Elo+12因子+凯利公式 | 数据源:500.com<br>⚠️ AI分析仅供参考，理性投注</div>
<script>
let data=[],cf='all';
async function load(){document.getElementById('content').innerHTML='<div class="loading"><div class="spinner"></div><p>加载中...</p></div>';
try{let r=await fetch('/api/matches');let d=await r.json();data=d.results;document.getElementById('ut').textContent='更新:'+d.time+' | '+d.count+'场';render()}catch(e){document.getElementById('content').innerHTML='<div style="text-align:center;padding:60px;color:#78909c">❌ 加载失败，请重试</div>'}}
function filter(f){cf=f;document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));event.target.classList.add('active');render()}
function render(){let items=cf==='all'?data:data.filter(d=>d.match.league.includes(cf));if(!items.length){document.getElementById('content').innerHTML='<div style="text-align:center;padding:60px;color:#78909c">暂无比赛</div>';return}
let h='';items.forEach(d=>{let m=d.match,p=d.model,po=d.poisson,fs=d.factor_score;
h+=`<div class="match-card" id="c-${m.id}" onclick="toggle('${m.id}','${m.home}','${m.away}')">
<div class="match-hdr"><span class="id">${m.id}</span><span class="league">${m.league}</span><span class="time">⏰ ${m.time}</span><span class="expand-icon">▼</span></div>
<div class="click-hint">点击展开深度分析</div>
<div class="teams"><div class="team"><div class="tn">${m.home}</div><div class="tr">[${m.hr}]</div></div><div class="vs">VS</div><div class="team"><div class="tn">${m.away}</div><div class="tr">[${m.ar}]</div></div></div>
<div class="odds-g"><div class="odds-b"><div class="l">${m.home}胜</div><div class="v">${m.spf_w.toFixed(2)}</div></div><div class="odds-b"><div class="l">平局</div><div class="v">${m.spf_d.toFixed(2)}</div></div><div class="odds-b"><div class="l">${m.away}胜</div><div class="v">${m.spf_l.toFixed(2)}</div></div></div>
<div class="prob-bar"><div class="pb-h" style="width:${p.home}%"></div><div class="pb-d" style="width:${p.draw}%"></div><div class="pb-a" style="width:${p.away}%"></div></div>
<div class="pl"><span>${m.home} ${p.home}%</span><span>平 ${p.draw}%</span><span>${m.away} ${p.away}%</span></div>
<div class="g2"><div class="box"><h4>泊松比分(λ=${po.hl}/${po.al})</h4><div class="chips">${po.scores.slice(0,5).map(s=>`<div class="chip">${s[0]}·${s[1].toFixed(1)}%</div>`).join('')}</div></div>
<div class="box"><h4>12因子</h4><div class="chips"><div class="chip">${m.home} ${fs.home}</div><div class="chip">${m.away} ${fs.away}</div></div><div style="margin-top:6px;font-size:0.8em">双方进球${d.btts}% | 大球${d.over}%</div></div></div>`;
d.recs.forEach(r=>{h+=r.startsWith('💰')?`<div class="val">${r}</div>`:`<div class="rec">${r}</div>`});
h+=`<div class="detail-panel" id="d-${m.id}"><div style="text-align:center;color:#546e7a;padding:20px">加载中...</div></div></div>`});
document.getElementById('content').innerHTML=h}
function refresh(){load()}
async function toggle(id,home,away){let p=document.getElementById('d-'+id),c=document.getElementById('c-'+id);if(c.classList.contains('expanded')){c.classList.remove('expanded');return}c.classList.add('expanded');if(p.innerHTML.includes('加载中')){try{let r=await fetch('/api/match_detail?home='+encodeURIComponent(home)+'&away='+encodeURIComponent(away));let d=await r.json();p.innerHTML=renderDetail(d)}catch(e){p.innerHTML='<div style="color:#ef5350;text-align:center">加载失败</div>'}}}
function renderDetail(d){let de=d.detail||{},h=`<div class="dsec"><h4>📋 ${de.title||(d.home+' vs '+d.away)}</h4></div>`;if(de.tactics){h+=`<div class="dg"><div class="dc"><div class="dn">🏴 ${de.tactics.home.name}</div><div class="dr">${de.tactics.home.formation||''}</div><div class="dc">${de.tactics.home.plan||''}</div><div class="dr" style="margin-top:4px">⭐ ${de.tactics.home.key||''}</div></div><div class="dc"><div class="dn">🏴 ${de.tactics.away.name}</div><div class="dr">${de.tactics.away.formation||''}</div><div class="dc">${de.tactics.away.plan||''}</div><div class="dr" style="margin-top:4px">⭐ ${de.tactics.away.key||''}</div></div></div>`}
if(de.goal_patterns){h+=`<div class="dsec"><h4>⚽ 进球模式</h4><div class="dg"><div class="dc"><div class="dn">${de.tactics?de.tactics.home.name:d.home}</div><div class="dc">${de.goal_patterns.home||''}</div></div><div class="dc"><div class="dn">${de.tactics?de.tactics.away.name:d.away}</div><div class="dc">${de.goal_patterns.away||''}</div></div></div></div>`}
if(de.referee){h+=`<div class="dsec"><h4>🦯 裁判</h4><div class="dc"><div class="dn">${de.referee.name}(${de.referee.nationality})</div><div class="dr">${de.referee.style}</div><div class="dc">${de.referee.detail||''}</div><div class="dc" style="color:#ffa726;margin-top:4px">→ ${de.referee.impact||''}</div></div></div>`}
if(de.venue&&de.venue.name!="待定"&&de.venue.name!="待公布"){h+=`<div class="dsec"><h4>🏟 场地</h4><div class="dc"><div class="dn">${de.venue.name}</div><div class="dc">${de.venue.detail||''}</div></div></div>`}
if(de.h2h){h+=`<div class="dsec"><h4>📜 交锋</h4><div class="dc"><div class="dc">${de.h2h}</div></div></div>`}
if(de.x_factor){h+=`<div class="dsec"><h4>🎲 X因素</h4><div class="dc" style="border-left:2px solid #ff9800"><div class="dc" style="color:#ffcc80">${de.x_factor}</div></div></div>`}
return h}
load()
</script>
</body>
</html>'''

if __name__=='__main__':
    port=int(os.environ.get('PORT',5026))
    print(f'Starting on port {port}...')
    app.run(host='0.0.0.0',port=port)
