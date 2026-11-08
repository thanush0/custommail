// Copyright (c) 2026 Thanush Priyan R.. All Rights Reserved.
// CustomMail | https://github.com/thanush0/custommail

"use strict";
const $ = (id) => document.getElementById(id);
const API = "/api/temp-mailboxes";
const TOKEN_KEY = "custommail_temp_mail_token";
let token = sessionStorage.getItem(TOKEN_KEY) || "";
let mailbox = null;
let selectedMessageId = null;
let pollTimer = null;
let clockTimer = null;
let busy = false;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  })[c]);
}
function say(message, error=false) {
  $("notice").textContent = message || "";
  $("notice").style.color = error ? "#c34444" : "#a15b19";
}
async function api(path, options={}) {
  const headers = {...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers, credentials:"same-origin", cache:"no-store"});
  let data = {};
  try { data = await response.json(); } catch {}
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}
function setBusy(value) {
  busy = value;
  $("generateButton").disabled = value;
  $("refreshButton").disabled = value || !token;
}
function fmtDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}
function updateExpiry() {
  if (!mailbox) return;
  const remaining = Math.max(0, new Date(mailbox.expires_at).getTime() - Date.now());
  if (!remaining) {
    $("expiryLabel").textContent = "Inbox expired";
    $("statusLabel").textContent = "Expired";
    $("statusLabel").style.color = "#c34444";
    stopPolling();
    return;
  }
  const total = Math.floor(remaining / 1000);
  const hours = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  $("expiryLabel").textContent = `Expires in ${hours}h ${mins}m ${secs}s`;
  $("statusLabel").textContent = "Active · Receiving";
  $("statusLabel").style.color = "#15996b";
}
function startTimers() {
  stopPolling();
  pollTimer = setInterval(() => loadMessages(true), 12000);
  clockTimer = setInterval(updateExpiry, 1000);
}
function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  if (clockTimer) clearInterval(clockTimer);
  pollTimer = clockTimer = null;
}
async function createMailbox() {
  if (busy) return;
  setBusy(true);
  say("Creating a fresh temporary inbox…");
  try {
    if (token) {
      try { await api(`${API}/current`, {method:"DELETE"}); } catch {}
    }
    token = "";
    sessionStorage.removeItem(TOKEN_KEY);
    mailbox = null;
    selectedMessageId = null;
    stopPolling();
    $("readerContent").hidden = true;
    $("readerEmpty").hidden = false;
    $("addressValue").textContent = "Creating your inbox…";
    const result = await api(API, {method:"POST"});
    token = result.token;
    sessionStorage.setItem(TOKEN_KEY, token);
    mailbox = result;
    $("addressValue").textContent = result.email;
    $("copyButton").disabled = false;
    $("refreshButton").disabled = false;
    $("messageCount").textContent = "0";
    $("messageList").innerHTML = `<div class="empty"><div class="empty-icon">✉</div><b>Your inbox is ready</b><p>Send an email to this address. It will appear here automatically.</p></div>`;
    updateExpiry();
    say("Your temporary email address is ready.");
    startTimers();
    await loadMessages();
  } catch (error) {
    $("addressValue").textContent = "Unable to create inbox";
    say(error.message, true);
  } finally {
    setBusy(false);
  }
}
async function restoreMailbox() {
  if (!token) return createMailbox();
  setBusy(true);
  let expired = false;
  try {
    mailbox = await api(`${API}/current`);
    $("addressValue").textContent = mailbox.email;
    $("copyButton").disabled = false;
    $("refreshButton").disabled = false;
    updateExpiry();
    startTimers();
    await loadMessages();
  } catch (error) {
    token = "";
    sessionStorage.removeItem(TOKEN_KEY);
    say(error.message, true);
    expired = true;
  } finally {
    setBusy(false);
  }
  if (expired) await createMailbox();
}
async function copyAddress() {
  if (!mailbox) return;
  try {
    await navigator.clipboard.writeText(mailbox.email);
    say("Address copied to clipboard.");
  } catch {
    const field = document.createElement("textarea");
    field.value = mailbox.email;
    document.body.appendChild(field);
    field.select();
    const copied = document.execCommand("copy");
    field.remove();
    say(copied ? "Address copied to clipboard." : "Copy failed. Select the address and copy it manually.", !copied);
  }
}
async function loadMessages(silent=false) {
  if (!token) return;
  try {
    const rows = await api(`${API}/current/messages?folder=all&limit=100`);
    $("messageCount").textContent = String(rows.length);
    if (!rows.length) {
      $("messageList").innerHTML = `<div class="empty"><div class="empty-icon">✉</div><b>Your inbox is empty</b><p>Waiting for incoming messages…</p></div>`;
    } else {
      $("messageList").innerHTML = rows.map(m => `
        <div class="message-row ${m.is_read ? "" : "unread"} ${m.id === selectedMessageId ? "selected" : ""}" data-id="${m.id}">
          <div class="message-icon">${m.is_read ? "✉" : "●"}</div>
          <div class="message-copy">
            <div class="sender">${esc(m.sender)}</div>
            <div class="subject">${esc(m.subject || "(no subject)")}</div>
            <div class="snippet">${esc((m.body || "").slice(0,100))}</div>
          </div>
          <div class="message-date">${esc(fmtDate(m.created_at))}</div>
        </div>`).join("");
      $("messageList").querySelectorAll(".message-row").forEach(row => {
        row.addEventListener("click", () => openMessage(Number(row.dataset.id)));
      });
      if (selectedMessageId && !rows.some(m => m.id === selectedMessageId)) {
        selectedMessageId = null;
        $("readerContent").hidden = true;
        $("readerEmpty").hidden = false;
      }
    }
    $("lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    if (!silent) say(rows.length ? `${rows.length} message${rows.length===1?"":"s"} in this inbox.` : "Inbox checked. No messages yet.");
  } catch (error) {
    if (error.message.includes("expired") || error.message.includes("410")) {
      stopPolling();
      say("This inbox has expired. Create a new address to continue.", true);
    } else if (!silent) say(error.message, true);
  }
}
async function openMessage(id) {
  try {
    const m = await api(`${API}/current/messages/${id}`);
    selectedMessageId = id;
    $("readerEmpty").hidden = true;
    $("readerContent").hidden = false;
    $("readSubject").textContent = m.subject || "(no subject)";
    $("readMeta").textContent = `From ${m.sender} · ${fmtDate(m.created_at)} · ${m.delivery_status}`;
    $("readBody").textContent = m.body || "(This message has no plain-text content.)";
    $("readAttachments").innerHTML = (m.attachments || []).map(a =>
      `<button class="attachment-button" data-attachment="${a.id}" data-filename="${esc(a.filename)}">↓ ${esc(a.filename)} · ${Math.ceil(a.size/1024)} KB</button>`
    ).join("");
    $("readAttachments").querySelectorAll("[data-attachment]").forEach(button => {
      button.addEventListener("click", () => downloadAttachment(button.dataset.attachment, button.dataset.filename));
    });
    await loadMessages(true);
  } catch (error) { say(error.message, true); }
}
async function downloadAttachment(id, filename) {
  try {
    const response = await fetch(`${API}/current/attachments/${id}`, {
      headers:{Authorization:`Bearer ${token}`}, cache:"no-store"
    });
    if (!response.ok) throw new Error("Attachment download failed.");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename || "attachment";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { say(error.message, true); }
}
async function deleteMessage() {
  if (!selectedMessageId) return;
  try {
    await api(`${API}/current/messages/${selectedMessageId}`, {method:"DELETE"});
    selectedMessageId = null;
    $("readerContent").hidden = true;
    $("readerEmpty").hidden = false;
    await loadMessages(true);
    say("Message deleted.");
  } catch (error) { say(error.message, true); }
}
$("copyButton").addEventListener("click", copyAddress);
$("generateButton").addEventListener("click", createMailbox);
$("refreshButton").addEventListener("click", () => loadMessages());
$("deleteMessageButton").addEventListener("click", deleteMessage);
restoreMailbox();
