import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
const logic = source.slice(source.indexOf('  async function sendMessage('), source.indexOf('  function toggleSources'));
function harness(fetch) {
  const state = { messages: [], input: 'Hello', sessionId: null };
  const names = ['messages','input','sessionId','loading','error','failedMessage','expandedSources','copiedIndex','notice'];
  const setters = names.map(name => value => { state[name] = typeof value === 'function' ? value(state[name]) : value; });
  const requestRef = { current: null };
  const create = new Function('fetch','window','BACKEND_URL','requestRef','copyTimerRef','followScrollRef','textareaRef','input','sessionId', ...names.map(n => 'set' + n[0].toUpperCase() + n.slice(1)), logic + '; return {sendMessage, resetSession};');
  return { state, ...create(fetch, globalThis, 'https://example.test', requestRef, {}, {}, {}, state.input, state.sessionId, ...setters) };
}
const answer = { ok: true, json: async () => ({ answer: 'Hello from NUST', session_id: 'session-1', sources: [null, { title: 'NUST' }] }) };
test('successful answer preserves contract and filters invalid sources', async () => {
  const app = harness(async (url, options) => { assert.equal(url, 'https://example.test/chat'); assert.deepEqual(JSON.parse(options.body), {message:'Hello',session_id:null}); return answer; });
  await app.sendMessage();
  assert.equal(app.state.messages.length, 2);
  assert.equal(app.state.messages[1].sources.length, 1);
  assert.equal(app.state.sessionId, 'session-1');
  assert.equal(app.state.loading, false);
});
test('new conversation aborts pending work and ignores stale answer', async () => {
  let resolve, signal;
  const app = harness((url, options) => { signal = options.signal; return new Promise(r => { resolve = r; }); });
  const pending = app.sendMessage();
  app.resetSession();
  assert.equal(signal.aborted, true);
  resolve(answer);
  await pending;
  assert.deepEqual(app.state.messages, []);
  assert.equal(app.state.sessionId, null);
  assert.equal(app.state.loading, false);
});
test('duplicate submissions are blocked while pending', async () => {
  let resolve, calls = 0;
  const app = harness(() => { calls++; return new Promise(r => {resolve = r;}); });
  const pending = app.sendMessage();
  await app.sendMessage();
  assert.equal(calls, 1);
  resolve(answer); await pending;
});
test('retry succeeds without duplicating the user message', async () => {
  let calls = 0;
  const app = harness(async () => ++calls === 1 ? {ok:false,status:429} : answer);
  await app.sendMessage();
  assert.match(app.state.error, /Too many requests/);
  await app.sendMessage(app.state.failedMessage, true);
  assert.equal(app.state.messages.length, 2);
  assert.equal(app.state.error, null);
});
test('malformed answers fail gracefully', async () => {
  const app = harness(async () => ({ok:true,json:async () => ({answer:{invalid:true}})}));
  await app.sendMessage();
  assert.match(app.state.error, /incomplete/);
  assert.equal(app.state.messages.length, 1);
});
test('source links reject executable schemes', () => {
  const fn = source.slice(source.indexOf('function safeSourceUrl'), source.indexOf('function sourceName'));
  const safe = new Function(fn + '; return safeSourceUrl;')();
  assert.equal(safe('javascript:alert(1)'), undefined);
  assert.equal(safe('data:text/html,bad'), undefined);
  assert.equal(safe('https://nust.edu.pk'), 'https://nust.edu.pk/');
});
