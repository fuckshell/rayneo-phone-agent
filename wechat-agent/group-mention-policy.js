const AT_ALL_RE = /@(?:所有人|All\b|all\b)/;
const AT_ALL_BLOB_RE = /(?:notify@all|@im\.chatroom|room_at_all)/i;

function parseAtUserList(raw) {
  const src = String(raw || "");
  if (!src) return [];
  const m =
    src.match(/<atuserlist><!\[CDATA\[([^\]]*)\]\]><\/atuserlist>/i) ||
    src.match(/<atuserlist>([^<]*)<\/atuserlist>/i);
  if (!m) return [];
  return m[1]
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function isGroupAtAll(text, msgSource = "") {
  const blob = `${text || ""}\n${msgSource || ""}`;
  if (AT_ALL_RE.test(blob)) return true;
  if (/全体成员/.test(blob) && /@/.test(blob)) return true;
  if (AT_ALL_BLOB_RE.test(msgSource || "")) return true;
  const users = parseAtUserList(msgSource);
  if (users.some((u) => /^(notify@all|all|@all)$/i.test(u) || u.includes("notify@all"))) return true;
  return false;
}

function createGroupMentionPolicy(loadSelfWxids, loadAtMeNames) {
  function isGroupAtMe(text, msgSource = "", event = {}) {
    const selfWxids = loadSelfWxids(event);
    const users = parseAtUserList(msgSource);
    for (const wxid of selfWxids) {
      if (users.some((u) => u === wxid || u.includes(String(wxid).replace(/^wxid_/, "")))) {
        return true;
      }
    }
    const visible = String(text || "");
    for (const name of loadAtMeNames()) {
      const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const re = new RegExp(`@${esc}(?:[\\s\\u2005\\u2009\\u00a0]|$|[，,。！？?!：:])`, "iu");
      if (re.test(visible)) return true;
    }
    return false;
  }

  /** @returns {{ ok: boolean, reason?: string }} */
  function groupAutoReplyAllowed(event, text) {
    const talker = event?.talker || "";
    if (!talker.includes("@chatroom")) return { ok: true };

    const msgSource = event.msg_source || event.message_source || event.source_xml || "";
    const body = String(text || "").trim();

    if (isGroupAtAll(body, msgSource)) {
      return { ok: false, reason: "group-at-all" };
    }
    if (!isGroupAtMe(body, msgSource, event)) {
      return { ok: false, reason: "group-not-at-me" };
    }
    return { ok: true };
  }

  return { isGroupAtMe, groupAutoReplyAllowed };
}

module.exports = { isGroupAtAll, createGroupMentionPolicy };
