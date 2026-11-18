const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createGroupMentionPolicy } = require('../group-mention-policy');

test('private messages pass without loading group identity', () => {
  const policy = createGroupMentionPolicy(assert.fail, assert.fail);
  assert.deepEqual(policy.groupAutoReplyAllowed({talker: 'private'}, 'hello'), {ok: true});
});

test('broadcasts take precedence over a personal mention', () => {
  const policy = createGroupMentionPolicy(assert.fail, assert.fail);
  const blocked = {ok: false, reason: 'group-at-all'};
  const event = {talker: 'room@chatroom'};
  assert.deepEqual(policy.groupAutoReplyAllowed(event, '@所有人 @Tester'), blocked);
  assert.deepEqual(policy.groupAutoReplyAllowed({...event, msg_source: 'notify@all'}, '@Tester'), blocked);
});

test('personal mentions can be structured or visible; an unmentioned group is skipped', () => {
  const policy = createGroupMentionPolicy(() => new Set(['self_test']), () => ['Test.Name']);
  const event = {talker: 'room@chatroom'};
  for (const key of ['source_xml', 'message_source', 'msg_source']) {
    const xml = '<atuserlist><![CDATA[other; self_test]]></atuserlist>';
    assert.deepEqual(policy.groupAutoReplyAllowed({...event, [key]: xml}, 'hello'), {ok: true});
  }
  assert.deepEqual(policy.groupAutoReplyAllowed(event, '@Test.Name，hello'), {ok: true});
  for (const text of ['hello', '@TestXName hello', '@Test.NameExtra']) {
    assert.deepEqual(policy.groupAutoReplyAllowed(event, text), {ok: false, reason: 'group-not-at-me'});
  }
});
