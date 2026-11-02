import subprocess
import unittest
from pathlib import Path


class TimelineViewTest(unittest.TestCase):
    def test_retired_plans_actual_execution_and_unknown_time(self):
        script = r'''
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {clean,text} from './pages/dashboard/shared/format.js';
import {parseTimeMinutes,formatDate} from './pages/dashboard/shared/utils.js';
const source = fs.readFileSync('pages/dashboard/app.js', 'utf8');
const helper = source.slice(source.indexOf('function timelineExecutionState('), source.indexOf('function setTimelineButtons('));
const body = source.slice(source.indexOf('function renderTimelineDisplay('), source.indexOf('function timelineEditorRow('));
const node = (tag, cls='', content='') => ({tag, cls, content, children: [],
  append(...items) { this.children.push(...items); },
  replaceChildren(...items) { this.children = items; }});
const list = node('ol');
const travel = [];
const ctx = {node, el:{timelineList:list}, clean,text,parseTimeMinutes,formatDate,
  TIMELINE_TIME_EMPTY_TEXT:'待定', empty:(s)=>node('div','',s),
  TIMELINE_EXECUTION_LABELS:{planned:'待进行',active:'进行中',completed:'已完成',skipped:'已跳过',cancelled:'已取消'},
  timelineTravelText:(item, previous)=>{ travel.push(previous); return ''; }};
vm.createContext(ctx); vm.runInContext(helper+body, ctx);
const items = [
 {time:'14:45',activity:'原35分钟散步',execution_state:'skipped',execution_reason:'被短走替代',duration_minutes:35},
 {time:'14:45',activity:'喝水',execution_state:'completed',actual_display_time:'14:46',actual_started_at:'2026-11-02 14:46:00',actual_completed_at:'2026-11-02 14:51:00',actual_duration_minutes:5,duration_minutes:5},
 {time:'14:51',activity:'短走',execution_state:'completed',actual_completed_at:'2026-11-02 15:12:00',duration_minutes:20},
];
ctx.renderTimelineDisplay(items);
const renderedText = n => [n.content,...n.children.map(renderedText)].join(' ');
assert.equal(list.children.length,3);
assert.equal(list.children[0].children[1].tag,'details');
assert.equal(list.children[0].children[1].children[0].tag,'summary');
assert.ok(renderedText(list.children[0]).includes('原计划 · 已跳过'));
assert.ok(renderedText(list.children[0]).includes('被短走替代'));
assert.equal(list.children[1].children[1].tag,'div');
assert.ok(renderedText(list.children[1]).includes('实际开始'));
assert.ok(renderedText(list.children[1]).includes('实际执行 5 分钟'));
assert.ok(renderedText(list.children[2]).includes('计划时间'));
assert.ok(!renderedText(list.children[2]).includes('实际执行 20'));
assert.equal(travel[1],undefined);
assert.equal(travel[2],items[1]);
const originalOrder = [...items];
ctx.renderTimelineDisplay([{...items[1],time:'14:30',actual_display_time:'15:30',actual_started_at:'2026-11-02 15:30:00'},items[2]]);
assert.ok(renderedText(list.children[0]).includes('短走'));
assert.deepEqual(items,originalOrder);
ctx.renderTimelineDisplay([{...items[0],execution_state:'cancelled',actual_started_at:'2026-11-02 14:45:00'}]);
assert.equal(list.children[0].children[1].tag,'div');
ctx.renderTimelineDisplay([]); assert.ok(renderedText(list).includes('暂无时间轴'));
const pair = source.slice(source.indexOf('export function currentTimelinePair('), source.indexOf('\nfunction ',source.indexOf('export function currentTimelinePair('))).replace('export function','function');
vm.runInContext(pair,ctx);
const active={time:'14:51',execution_state:'active'}, future={time:'15:30',execution_state:'planned'};
const result=ctx.currentTimelinePair({date:'2026-11-02',timeline:[items[0],items[1],active,future]},new Date('2026-11-02T15:00:00'));
assert.equal(result.current,active);assert.equal(result.next,future);
assert.equal(ctx.cloneTimeline(items)[0].execution_state,'skipped');
console.log('timeline DOM behavior passed');
'''
        result = subprocess.run(['node', '--input-type=module'],
                                cwd=Path(__file__).resolve().parents[1], input=script,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
