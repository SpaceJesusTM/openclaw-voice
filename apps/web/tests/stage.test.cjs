// Voice stage: which state the orb draws, and how audio becomes bar heights.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { targets, stateOf, BARS } = require(path.join(__dirname, '..', 'stage.js'));

const orbWith = (...classes) => ({ classList: { contains: (c) => classes.includes(c) } });

test('reads the state app.js writes on the orb', () => {
  assert.deepEqual(stateOf(orbWith('orb', 'state-speaking')), { state: 'speaking', working: false, waiting: false });
  assert.deepEqual(stateOf(orbWith('orb', 'state-listening', 'working')), { state: 'listening', working: true, waiting: false });
  assert.deepEqual(stateOf(orbWith('orb', 'state-idle', 'thinking', 'waiting')), { state: 'thinking', working: false, waiting: true });
  assert.equal(stateOf(orbWith('orb', 'state-offline')).state, 'offline');
  assert.equal(stateOf(orbWith('orb', 'state-speaking', 'thinking')).state, 'speaking', 'speech wins over thinking');
});

test('live audio drives a symmetric ring; silence and offline stay low', () => {
  const bins = new Uint8Array(128).map((_, i) => (i < 20 ? 220 : 10));
  const speaking = targets({ state: 'speaking', bins, time: 0 });
  assert.equal(speaking.length, BARS);
  assert.ok(Math.max(...speaking) > 0.8, 'loud speech makes tall bars');
  for (let i = 0; i < BARS / 2; i++) assert.equal(speaking[i], speaking[BARS - 1 - i], 'mirrored left and right');

  const quiet = targets({ state: 'listening', bins: new Uint8Array(128), time: 0 });
  assert.ok(Math.max(...quiet) === 0, 'no sound, no bars');
  assert.ok(Math.max(...targets({ state: 'offline', bins: null, time: 0 })) < 0.05);
  assert.ok(Math.max(...targets({ state: 'idle', bins: null, time: 0 })) < 0.1);
});

test('thinking ripples gently, and not at all with reduced motion', () => {
  const ripple = targets({ state: 'thinking', bins: null, time: 1 });
  assert.ok(Math.max(...ripple) > Math.min(...ripple));
  const still = targets({ state: 'thinking', bins: null, time: 1, reduced: true });
  assert.equal(Math.max(...still), Math.min(...still));
});
