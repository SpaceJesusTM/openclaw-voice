// Browser mic gate behavior, without providers or a physical microphone.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function setup() {
  const nodes = new Map();
  const element = () => ({ checked: false, disabled: false, handlers: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
    setAttribute() {}, setPointerCapture() {} });
  const window = element();
  const document = Object.assign(element(), {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
  });
  let constraints;
  const context = vm.createContext({ window, document, navigator: { mediaDevices: {
    async getUserMedia(value) { constraints = value; return {}; },
  } }, setInterval, clearInterval });
  const source = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8').replace(/init\(\);\s*$/, '');
  vm.runInContext(source, context);
  vm.runInContext('const testTrack = {enabled: true}; ui.stream = {getAudioTracks: () => [testTrack]}; ui.connected = true;', context);
  return { nodes, window, document, run: (s) => vm.runInContext(s, context), constraints: () => constraints };
}
test('PTT gates audio until held, releases on lost focus/pointer, and permits barge-in', () => {
  const t = setup();
  t.nodes.get('ptt-mode').checked = true;
  t.nodes.get('ptt-mode').handlers.change();
  assert.equal(t.run('testTrack.enabled'), false);
  t.nodes.get('push-talk').handlers.pointerdown({ preventDefault() {}, pointerId: 1 });
  assert.equal(t.run('testTrack.enabled'), true);
  t.nodes.get('push-talk').handlers.pointerup();
  assert.equal(t.run('testTrack.enabled'), false);
  t.window.handlers.keydown({ code: 'Space', repeat: false, preventDefault() {}, target: {closest() {return null;}} });
  assert.equal(t.run('testTrack.enabled'), true);
  t.window.handlers.blur();
  assert.equal(t.run('testTrack.enabled'), false);
  t.window.handlers.keydown({ code: 'Space', target: {closest() {return {};}} });
  assert.equal(t.run('testTrack.enabled'), false, 'typing does not open mic');
  t.nodes.get('ptt-mode').checked = false;
  t.nodes.get('ptt-mode').handlers.change();
  assert.equal(t.run('testTrack.enabled'), true, 'natural open-mic barge-in remains available');
  t.nodes.get('mute').handlers.click();
  assert.equal(t.run('testTrack.enabled'), false);
});
test('typed-only never transmits mic; constraints request suppression without automatic gain', async () => {
  const t = setup();
  t.nodes.get('typed-only').checked = true;
  t.run('applyMicGate(true)');
  assert.equal(t.run('testTrack.enabled'), false);
  await t.run('getMicrophone()');
  assert.equal(t.constraints().audio.echoCancellation, true);
  assert.equal(t.constraints().audio.noiseSuppression, true);
  assert.equal(t.constraints().audio.autoGainControl, false);
});
