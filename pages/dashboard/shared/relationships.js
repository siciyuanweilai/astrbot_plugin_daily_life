import { clean, text } from "./format.js";

const RELATIONSHIP_REFERENCE_PREFIXES = [
  "profile",
  "relationship",
  "group_profile",
  "群友档案",
  "关系",
];

function objectItems(items) {
  return (Array.isArray(items) ? items : []).filter(
    (item) => item && typeof item === "object",
  );
}

function relationshipDisplayName(item = {}) {
  return clean(
    item.display_name || item.subjective_name || item.name || item.alias || "",
    "",
  );
}

function addRelationshipName(index, key, label) {
  const raw = text(key).trim();
  const name = text(label).trim();
  if (!raw || !name || raw === name) return;
  index.set(raw, name);
  const parts = raw.split(/[:：]/).map((part) => part.trim()).filter(Boolean);
  const id = parts.length > 1 ? parts[parts.length - 1] : raw;
  if (id && id !== name) index.set(id, name);
  RELATIONSHIP_REFERENCE_PREFIXES.forEach((prefix) => {
    index.set(`${prefix}:${id}`, name);
  });
}

function addGroupScopeName(index, key, label) {
  const raw = text(key).trim();
  const name = text(label).trim();
  if (!raw || !name || raw === name) return;
  if (!index.has(raw)) index.set(raw, name);
  const parts = raw.split(/[:：]/).map((part) => part.trim()).filter(Boolean);
  const id = parts.length > 1 ? parts[parts.length - 1] : raw;
  if (id && id !== name && !index.has(id)) index.set(id, name);
}

export function relationshipNameIndex(status = {}) {
  const index = new Map();
  objectItems(status.world?.relationships).forEach((item) => {
    const label = relationshipDisplayName(item);
    if (!label) return;
    ["id", "user_id", "profile_id", "target_scope"].forEach((key) => {
      addRelationshipName(index, item[key], label);
    });
    objectItems(item.contacts).forEach((contact) => {
      ["user_id", "target_scope", "profile_id"].forEach((key) => {
        addRelationshipName(index, contact[key], label);
      });
    });
  });
  objectItems(status.world?.group_environments).forEach((item) => {
    const label = clean(item.group_name, "");
    if (!label) return;
    ["group_id", "session_id"].forEach((key) => {
      addGroupScopeName(index, item[key], label);
    });
  });
  return index;
}

function resolveRelationshipReference(value, relationshipNames = new Map()) {
  const raw = text(value).trim();
  if (!raw) return "";
  const direct = relationshipNames.get(raw);
  if (direct) return direct;
  const parts = raw.split(/[:：]/).map((part) => part.trim()).filter(Boolean);
  if (parts.length > 1) {
    const id = parts[parts.length - 1];
    const byId = relationshipNames.get(id)
      || relationshipNames.get(`${parts[0]}:${id}`);
    if (byId) return byId;
  }
  return "";
}

export function relationshipScopeLabel(value, relationshipNames = new Map()) {
  const raw = text(value).trim();
  if (!raw) return "";
  const resolved = resolveRelationshipReference(raw, relationshipNames);
  return resolved || clean(raw, "");
}

export function relationshipReferenceText(value, relationshipNames = new Map()) {
  const raw = text(value).trim();
  if (!raw) return "";
  const resolved = relationshipNames.get(raw);
  if (resolved) return resolved;
  const prefixPattern = RELATIONSHIP_REFERENCE_PREFIXES
    .map((prefix) => prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  const pattern = new RegExp(
    `(^|[\\s,，:：;；、|｜()（）\\[\\]{}<>《》])(${prefixPattern})[:：]([^\\s,，:：;；、|｜()（）\\[\\]{}<>《》]+)`,
    "g",
  );
  const body = raw.replace(pattern, (match, lead, prefix, id) => {
    const name = resolveRelationshipReference(
      `${prefix}:${id}`,
      relationshipNames,
    );
    return name ? `${lead}${name}` : match;
  });
  return body === raw ? clean(body, "") : body;
}

export function relationshipTextResolver(status = {}) {
  const names = relationshipNameIndex(status);
  return {
    names,
    scope: (value) => relationshipScopeLabel(value, names),
    text: (value) => relationshipReferenceText(value, names),
  };
}

export function relationshipRecordLines(items, relationshipText) {
  return items.map((item) => (
    Array.isArray(item)
      ? [item[0], relationshipText(item[1])]
      : relationshipText(item)
  ));
}
