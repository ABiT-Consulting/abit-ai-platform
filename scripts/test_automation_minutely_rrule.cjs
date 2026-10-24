'use strict';
// Run with: node --test scripts/test_automation_minutely_rrule.cjs
// Exercise the actual Svelte builders so both save and Custom transitions are covered.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = fs.readFileSync(
  path.join(__dirname, '../src/lib/components/automations/ScheduleDropdown.svelte'), 'utf8'
);
const builders = ['buildRrule', 'buildVisualRrule'];
const bodies = Object.fromEntries(builders.map((name) => {
  const match = source.match(new RegExp(
    '(?:export )?const ' + name + ' = \\(\\): string => \\{([\\s\\S]*?)\\n\\t\\};'
  ));
  assert.ok(match, name + ' source anchor must exist');
  return [name, match[1]];
}));

function build(name, values = {}) {
  const state = {
    frequency: 'MINUTELY', lastVisualFrequency: 'MINUTELY', interval: 15,
    hour: 9, minute: 0, selectedDays: ['MO', 'FR'], monthDay: 24,
    onceDate: '2026-11-01', onceTime: '09:30', customRrule: '', ...values
  };
  return vm.runInNewContext('(() => {' + bodies[name] + '})()', state);
}

for (const name of builders) {
  test(name + ': 15-minute schedules ignore inherited time-of-hour filters', () => {
    for (const minute of [0, 7, 37, 59]) {
      assert.equal(build(name, { minute }), 'RRULE:FREQ=MINUTELY;INTERVAL=15');
    }
  });

  test(name + ': minute intervals retain their requested cadence', () => {
    for (const interval of [1, 5, 15, 30, 60, 1440]) {
      const expected = 'RRULE:FREQ=MINUTELY' + (interval > 1 ? ';INTERVAL=' + interval : '');
      assert.equal(build(name, { interval, minute: 45 }), expected);
    }
  });

  test(name + ': other visual schedule types retain their time filters', () => {
    const cases = {
      HOURLY: 'RRULE:FREQ=HOURLY;BYMINUTE=37',
      DAILY: 'RRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=37',
      WEEKLY: 'RRULE:FREQ=WEEKLY;BYDAY=MO,FR;BYHOUR=9;BYMINUTE=37',
      MONTHLY: 'RRULE:FREQ=MONTHLY;BYMONTHDAY=24;BYHOUR=9;BYMINUTE=37',
      ONCE: 'DTSTART:20261101T093000\nRRULE:FREQ=DAILY;COUNT=1'
    };
    for (const [frequency, expected] of Object.entries(cases)) {
      assert.equal(build(name, { frequency, lastVisualFrequency: frequency, interval: 1, minute: 37 }), expected);
    }
  });
}

test('explicit Custom rules are preserved verbatim', () => {
  const customRrule = 'DTSTART:20261101T000000\nRRULE:FREQ=MINUTELY;INTERVAL=15;BYMINUTE=0,15,30,45;COUNT=8';
  assert.equal(build('buildRrule', { frequency: 'CUSTOM', customRrule }), customRrule);
});
