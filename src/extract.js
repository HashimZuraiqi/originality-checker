"use strict";

// Plain-text extraction for uploaded files: .txt/.md as UTF-8, .docx by
// reading word/document.xml straight out of the ZIP container (no deps).

const zlib = require("node:zlib");

const MAX_UNCOMPRESSED = 50 * 1024 * 1024;

class ExtractError extends Error {}

function findEndOfCentralDirectory(buf) {
  // The EOCD record is 22 bytes plus an optional comment of up to 64 KiB.
  const min = Math.max(0, buf.length - 22 - 0xffff);
  for (let i = buf.length - 22; i >= min; i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) return i;
  }
  throw new ExtractError("Not a valid .docx file (ZIP directory not found).");
}

function readZipEntry(buf, wanted) {
  const eocd = findEndOfCentralDirectory(buf);
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  for (let n = 0; n < count; n++) {
    if (p + 46 > buf.length || buf.readUInt32LE(p) !== 0x02014b50) {
      throw new ExtractError("Corrupt .docx file (bad ZIP directory).");
    }
    const method = buf.readUInt16LE(p + 10);
    const compSize = buf.readUInt32LE(p + 20);
    const size = buf.readUInt32LE(p + 24);
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    const localOffset = buf.readUInt32LE(p + 42);
    const name = buf.toString("utf8", p + 46, p + 46 + nameLen);
    p += 46 + nameLen + extraLen + commentLen;
    if (name !== wanted) continue;

    if (localOffset + 30 > buf.length || buf.readUInt32LE(localOffset) !== 0x04034b50) {
      throw new ExtractError("Corrupt .docx file (bad ZIP entry).");
    }
    const dataStart =
      localOffset + 30 + buf.readUInt16LE(localOffset + 26) + buf.readUInt16LE(localOffset + 28);
    const data = buf.subarray(dataStart, dataStart + compSize);
    if (data.length !== compSize) throw new ExtractError("Corrupt .docx file (truncated).");
    if (size > MAX_UNCOMPRESSED) throw new ExtractError("Document is too large.");
    if (method === 0) return data;
    if (method === 8) {
      try {
        return zlib.inflateRawSync(data, { maxOutputLength: MAX_UNCOMPRESSED });
      } catch {
        throw new ExtractError("Corrupt .docx file (could not decompress).");
      }
    }
    throw new ExtractError("Unsupported .docx compression method.");
  }
  throw new ExtractError("Not a Word document (word/document.xml missing).");
}

function decodeEntities(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (whole, e) => {
    const k = e.toLowerCase();
    if (k === "amp") return "&";
    if (k === "lt") return "<";
    if (k === "gt") return ">";
    if (k === "quot") return '"';
    if (k === "apos") return "'";
    const code = k.startsWith("#x") ? parseInt(k.slice(2), 16) : parseInt(k.slice(1), 10);
    return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
  });
}

function docxXmlToText(xml) {
  let out = "";
  const re = /<w:t(?:\s[^>]*)?>([^<]*)<\/w:t>|<w:tab\s*\/>|<w:(?:br|cr)(?:\s[^>]*)?\/>|<\/w:p>/g;
  for (const m of xml.matchAll(re)) {
    if (m[1] !== undefined) out += decodeEntities(m[1]);
    else if (m[0].startsWith("<w:tab")) out += "\t";
    else if (m[0] === "</w:p>") out += "\n";
    else out += "\n";
  }
  return out.replace(/\n{3,}/g, "\n\n").trim();
}

function extractText(filename, buffer) {
  const ext = String(filename || "").toLowerCase().split(".").pop();
  if (ext === "txt" || ext === "md" || ext === "text") {
    return buffer.toString("utf8").replace(/^﻿/, "");
  }
  if (ext === "docx") {
    return docxXmlToText(readZipEntry(buffer, "word/document.xml").toString("utf8"));
  }
  throw new ExtractError("Unsupported file type. Upload a .txt, .md or .docx file.");
}

module.exports = { extractText, docxXmlToText, ExtractError };
