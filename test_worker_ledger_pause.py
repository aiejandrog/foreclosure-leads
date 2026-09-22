"""Run the shipped worker's JavaScript against a fake bridge, never real recipients."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


class WorkerLedgerPause(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parent
        # Reuse only the existing test's extraction helpers, without executing its suite.
        tree = ast.parse((root / '_autorunstoptest.py').read_text(encoding='utf-8'))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in ('node', 'extract', 'worker_blob_js')]
        cls.helpers = dict(json=json, os=os, re=re, subprocess=subprocess, tempfile=tempfile)
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<worker-test-helpers>', 'exec'), cls.helpers)
        cls.js, _ = cls.helpers['worker_blob_js']((root / 'tracker_template.html').read_text(encoding='utf-8'))

    def run_js(self, actions):
        functions = '\n'.join(self.helpers['extract'](self.js, n)
                              for n in ('doSend', 'probeBridge', 'pause', 'startRun'))
        harness = r'''
var BRIDGE_HOLD='', BRIDGE_OK=true, auto=true, autoAll=true, nextStep=null;
var sending=false, sendingAt=0, i=4, Q=Array(12).fill({mailTo:'fixture@example.invalid'});
var lane='urgent', marks=[], logs=[], requests=0, timers=[], runs=0, tbOn=false;
var LMETA={urgent:{t:'URGENT'}}, CAP={max:50}, AbortController=undefined;
var reply={status:200,j:{ok:false,blocked:'optout_stale',err:'ledger stale'}};
function fetch(){requests++; return Promise.resolve({status:reply.status,json:()=>Promise.resolve(reply.j)});}
function setTimeout(fn){timers.push(fn); return timers.length;}
function clearTimeout(){} function render(){} function renderBridge(){} function renderRunBar(){}
function addLog(k,n,s){logs.push(k);} function post(k){marks.push(k);}
function advance(){i++;} function bridgeUp(ok){BRIDGE_OK=ok;} function _cacheLive(){}
function sendInFlight(){return sending;} function sentToday(){return 0;}
function runOne(){runs++;}
var lead={mailSubj:'Fixture',mailBody:'Fixture',mailTo:'fixture@example.invalid'};
'''
        return self.helpers['node'](harness + functions + '\n(async()=>{' + actions + r'''
await new Promise(resolve=>setImmediate(resolve));
console.log(JSON.stringify({auto,autoAll,i,marks,logs,requests,timers:timers.length,runs,hold:BRIDGE_HOLD}));
})().catch(e=>{console.error(e);process.exit(1);});''')

    def test_stale_rejection_keeps_current_lead_and_posts_no_skip(self):
        out = self.run_js('doSend(lead);')
        self.assertEqual(out['i'], 4)
        self.assertEqual(out['marks'], [])
        self.assertEqual(out['requests'], 1)
        self.assertEqual(out['timers'], 0)
        self.assertFalse(out['auto'])
        self.assertFalse(out['autoAll'])
        self.assertTrue(out['hold'])

    def test_preflight_stale_blocks_start_without_sending(self):
        out = self.run_js("auto=false; reply.j={ready:true,optout_stale:true}; probeBridge(); await new Promise(r=>setImmediate(r)); startRun(false,'test');")
        self.assertFalse(out['auto'])
        self.assertEqual(out['runs'], 0)
        self.assertEqual(out['marks'], [])
        self.assertTrue(out['hold'])

    def test_fresh_health_clears_warning_without_automatic_resume(self):
        out = self.run_js("auto=false; BRIDGE_HOLD='stale'; reply.j={ready:true,optout_stale:false}; probeBridge();")
        self.assertEqual(out['hold'], '')
        self.assertFalse(out['auto'])
        self.assertEqual(out['runs'], 0)

    def test_success_still_records_sent(self):
        out = self.run_js("reply.j={ok:true,message_id:'fixture',sent_today:1}; doSend(lead);")
        self.assertEqual(out['marks'], ['sent'])
        self.assertEqual(out['timers'], 1)

    def test_known_hold_prevents_a_second_request(self):
        out = self.run_js("BRIDGE_HOLD='stale'; doSend(lead);")
        self.assertEqual(out['requests'], 0)
        self.assertEqual(out['marks'], [])
        self.assertFalse(out['auto'])


if __name__ == '__main__':
    unittest.main()
