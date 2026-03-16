const state = {
  documents: [],
  activeDocumentId: null,
  pollTimer: null,
};

const documentList = document.getElementById("documentList");
const docCount = document.getElementById("docCount");
const activeDocumentName = document.getElementById("activeDocumentName");
const sessionBadge = document.getElementById("sessionBadge");
const statusText = document.getElementById("statusText");
const progressBar = document.getElementById("progressBar");
const errorText = document.getElementById("errorText");
const sourcePane = document.getElementById("sourcePane");
const translationPane = document.getElementById("translationPane");
const translateButton = document.getElementById("translateButton");
const downloadButton = document.getElementById("downloadButton");
const deleteButton = document.getElementById("deleteButton");
const targetLanguage = document.getElementById("targetLanguage");
const fileInput = document.getElementById("fileInput");
const dropzone = document.getElementById("dropzone");
const itemTemplate = document.getElementById("documentItemTemplate");

function formatStatus(document) {
  if (!document) return "Waiting for upload";
  if (document.status === "ready") return "Parsed and ready";
  if (document.status === "translating") return "Translating";
  if (document.status === "completed") return "Completed";
  if (document.status === "failed") return "Failed";
  return document.status;
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function hasTranslatedContent(document) {
  if (!document) return false;
  return document.blocks.some(
    (block) =>
      (block.type !== "page_break" && Boolean(block.translated_text)) ||
      Boolean(block.translated_caption) ||
      (Array.isArray(block.translated_rows) && block.translated_rows.length > 0),
  );
}

function renderImageBlock(block, translated) {
  const caption = translated
    ? block.translated_caption || block.caption || ""
    : block.caption || "";
  const captionMarkup = caption
    ? `<figcaption class="doc-caption">${escapeHtml(caption)}</figcaption>`
    : "";
  const captionFirst = block.caption_position === "above";
  const sideCaption = block.caption_position === "left" || block.caption_position === "right";

  if (sideCaption) {
    const figureClass = `doc-block doc-figure side-caption caption-${block.caption_position}`;
    return `
      <figure class="${figureClass}">
        <img class="doc-image" src="${block.image_url}" alt="" />
        ${captionMarkup}
      </figure>
    `;
  }

  return `
    <figure class="doc-block doc-figure">
      ${captionFirst ? captionMarkup : ""}
      <img class="doc-image" src="${block.image_url}" alt="" />
      ${captionFirst ? "" : captionMarkup}
    </figure>
  `;
}

function renderBlocks(blocks, translated) {
  if (!blocks || !blocks.length) {
    return translated ? "No translated content yet." : "No parsed source content.";
  }

  return blocks
    .map((block) => {
      if (block.type === "heading") {
        const level = Math.min(Math.max(block.level || 1, 1), 4);
        const text = translated ? block.translated_text || "" : block.text || "";
        return `<section class="doc-block"><h${level} class="doc-heading">${escapeHtml(text)}</h${level}></section>`;
      }

      if (block.type === "paragraph") {
        const text = translated ? block.translated_text || "" : block.text || "";
        return `<section class="doc-block"><p class="doc-paragraph">${escapeHtml(text)}</p></section>`;
      }

      if (block.type === "page_break") {
        const text = translated ? block.translated_text || block.text || "" : block.text || "";
        return `<section class="doc-block"><p class="page-break">${escapeHtml(text)}</p></section>`;
      }

      if (block.type === "image") {
        return renderImageBlock(block, translated);
      }

      if (block.type === "table") {
        const rows = translated ? block.translated_rows || [] : block.rows || [];
        const body = rows
          .map(
            (row) =>
              `<tr>${row
                .map((cell) => `<td>${escapeHtml(cell || "")}</td>`)
                .join("")}</tr>`,
          )
          .join("");
        return `<section class="doc-block"><table class="doc-table"><tbody>${body}</tbody></table></section>`;
      }

      return "";
    })
    .join("");
}

function getActiveDocument() {
  return state.documents.find((item) => item.id === state.activeDocumentId) || null;
}

function updateDocumentList() {
  documentList.innerHTML = "";
  docCount.textContent = String(state.documents.length);

  state.documents.forEach((document) => {
    const node = itemTemplate.content.firstElementChild.cloneNode(true);
    node.querySelector(".document-name").textContent = document.name;
    node.querySelector(".document-meta").textContent = `${document.kind.toUpperCase()} · ${formatStatus(document)}`;
    node.classList.toggle("active", document.id === state.activeDocumentId);
    node.addEventListener("click", () => {
      state.activeDocumentId = document.id;
      renderActiveDocument();
      updateDocumentList();
      schedulePolling();
    });
    documentList.appendChild(node);
  });
}

function renderActiveDocument() {
  const activeDocument = getActiveDocument();
  translateButton.disabled = !activeDocument;
  downloadButton.disabled = !hasTranslatedContent(activeDocument);
  deleteButton.disabled = !activeDocument;
  errorText.textContent = activeDocument?.error || "";
  statusText.textContent = formatStatus(activeDocument);
  progressBar.style.width = `${Math.round((activeDocument?.progress || 0) * 100)}%`;

  if (!activeDocument) {
    activeDocumentName.textContent = "No document selected";
    sourcePane.classList.add("empty-state");
    translationPane.classList.add("empty-state");
    sourcePane.textContent = "Upload a document to inspect its structured source content.";
    translationPane.textContent = "Run translation to render the translated structure here.";
    sessionBadge.classList.add("hidden");
    return;
  }

  activeDocumentName.textContent = activeDocument.name;
  sourcePane.classList.remove("empty-state");
  translationPane.classList.remove("empty-state");
  sourcePane.innerHTML = renderBlocks(activeDocument.blocks, false);
  translationPane.innerHTML = renderBlocks(activeDocument.blocks, true);

  if (activeDocument.session) {
    sessionBadge.classList.remove("hidden");
    sessionBadge.textContent = `Session ${activeDocument.session.id.slice(0, 8)} · ${activeDocument.target_language || activeDocument.session.target_language}`;
  } else {
    sessionBadge.classList.add("hidden");
  }
}

async function loadDocuments() {
  const response = await fetch("/api/documents");
  if (!response.ok) {
    throw new Error("Unable to load document list.");
  }

  state.documents = await response.json();
  if (!state.documents.some((item) => item.id === state.activeDocumentId)) {
    state.activeDocumentId = state.documents[0]?.id || null;
  }
  updateDocumentList();
  renderActiveDocument();
  schedulePolling();
}

async function uploadFiles(fileList) {
  if (!fileList.length) return;
  const formData = new FormData();
  [...fileList].forEach((file) => formData.append("files", file));

  const response = await fetch("/api/documents", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Upload failed.");
  }
  await loadDocuments();
}

async function translateActiveDocument() {
  const activeDocument = getActiveDocument();
  if (!activeDocument) return;

  translateButton.disabled = true;
  const response = await fetch(`/api/documents/${activeDocument.id}/translate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_language: targetLanguage.value.trim() || "Chinese (Simplified)" }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Unable to start translation.");
  }
  await loadDocuments();
}

async function deleteActiveDocument() {
  const activeDocument = getActiveDocument();
  if (!activeDocument) return;

  const confirmed = window.confirm(`Delete "${activeDocument.name}" and its extracted files?`);
  if (!confirmed) return;

  const response = await fetch(`/api/documents/${activeDocument.id}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Unable to delete document.");
  }

  if (state.activeDocumentId === activeDocument.id) {
    state.activeDocumentId = null;
  }
  await loadDocuments();
}

function downloadActiveTranslation() {
  const activeDocument = getActiveDocument();
  if (!activeDocument || !hasTranslatedContent(activeDocument)) return;

  const link = window.document.createElement("a");
  link.href = `/api/documents/${activeDocument.id}/translation.txt`;
  link.rel = "noopener";
  window.document.body.appendChild(link);
  link.click();
  link.remove();
}

async function refreshActiveDocument() {
  const activeDocument = getActiveDocument();
  if (!activeDocument) return;

  const response = await fetch(`/api/documents/${activeDocument.id}`);
  if (!response.ok) return;

  const payload = await response.json();
  const index = state.documents.findIndex((item) => item.id === payload.id);
  if (index >= 0) {
    state.documents[index] = payload;
  } else {
    state.documents.unshift(payload);
  }
  updateDocumentList();
  renderActiveDocument();
}

function schedulePolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  const activeDocument = getActiveDocument();
  if (!activeDocument || activeDocument.status !== "translating") {
    return;
  }

  state.pollTimer = setInterval(async () => {
    await refreshActiveDocument();
    const current = getActiveDocument();
    if (!current || current.status !== "translating") {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }, 2000);
}

dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragover");
});

dropzone.addEventListener("drop", async (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragover");
  try {
    await uploadFiles(event.dataTransfer.files);
  } catch (error) {
    errorText.textContent = error.message;
  }
});

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});

fileInput.addEventListener("change", async () => {
  try {
    await uploadFiles(fileInput.files);
    fileInput.value = "";
  } catch (error) {
    errorText.textContent = error.message;
  }
});

translateButton.addEventListener("click", async () => {
  try {
    await translateActiveDocument();
  } catch (error) {
    errorText.textContent = error.message;
  }
});

downloadButton.addEventListener("click", () => {
  try {
    downloadActiveTranslation();
  } catch (error) {
    errorText.textContent = error.message;
  }
});

deleteButton.addEventListener("click", async () => {
  try {
    await deleteActiveDocument();
  } catch (error) {
    errorText.textContent = error.message;
  }
});

loadDocuments().catch((error) => {
  errorText.textContent = error.message;
});
