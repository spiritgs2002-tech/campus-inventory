"use strict";
document.addEventListener("click", async function (event) {
  const button = event.target.closest("[data-observe-stock]");
  if (!button) return;
  const row = button.closest("fieldset");
  const field = name => row.querySelector('[name$="-' + name + '"]');
  const message = row.querySelector("[data-observe-message]");
  const values = {item_id: field("item_id").value, location_id: field("location_id").value, quality: field("quality").value};
  if (!values.item_id || !values.location_id || !values.quality) { message.textContent = "请先选择物资、库位和质量"; return; }
  button.disabled = true;
  try {
    const getSnapshot = async quality => {
      const response = await fetch("/api/v1/inventory/observe/?" + new URLSearchParams({...values, quality}), {credentials: "same-origin"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || "观察失败，请重新登录或稍后重试");
      return data;
    };
    const data = await getSnapshot(values.quality);
    field("observed_at").value = data.observed_at;
    if (data.item_type === "fixed") {
      ["book_qty", "counted_qty", "inventory_version"].forEach(name => { if (field(name)) field(name).value = ""; });
      field("direction").value = "in";
      field("quantity").value = "1";
      message.textContent = "已记录观察时间；固定资产盘盈按一件申请";
    } else {
      field("book_qty").value = data.book_qty;
      field("inventory_version").value = data.inventory_version;
      if (field("to_quality")) {
        if (!field("to_quality").value) throw new Error("请选择转换后的质量，再重新观察");
        const target = await getSnapshot(field("to_quality").value);
        field("target_inventory_version").value = target.inventory_version;
        field("direction").value = "out";
      }
      message.textContent = "账面数量已观察，请填写实盘差额或转换数量；审批时将复核快照";
    }
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; }
});
document.addEventListener("input", function (event) {
  if (!event.target.name?.endsWith("-counted_qty")) return;
  const row = event.target.closest("fieldset");
  const book = row.querySelector('[name$="-book_qty"]');
  const amount = row.querySelector('[name$="-quantity"]');
  const direction = row.querySelector('[name$="-direction"]');
  if (!book?.value || event.target.value === "") return;
  const delta = Number(event.target.value) - Number(book.value);
  if (Number.isFinite(delta)) { amount.value = Math.abs(delta).toFixed(3); direction.value = delta >= 0 ? "in" : "out"; }
});
document.addEventListener("click", function (event) {
  if (!event.target.closest("[data-add-order-line]")) return;
  const form = event.target.closest("[data-order-form]");
  const total = form.querySelector("[name=lines-TOTAL_FORMS]");
  if (Number(total.value) >= 50) return;
  const fragment = form.querySelector("[data-empty-order-line]").content.cloneNode(true);
  fragment.querySelectorAll("[name], [id], [for]").forEach(function (element) {
    ["name", "id", "for"].forEach(function (attribute) {
      if (element.hasAttribute(attribute)) element.setAttribute(attribute, element.getAttribute(attribute).replaceAll("__prefix__", total.value));
    });
  });
  total.value = Number(total.value) + 1;
  form.querySelector("[data-order-lines]").append(fragment);
});
document.addEventListener("htmx:configRequest", function (event) {
  const form = event.detail.elt.closest("form");
  if (!form) return;
  const token = form.querySelector("[name=csrfmiddlewaretoken]");
  const key = form.querySelector("[name=idempotency_key]");
  if (token) event.detail.headers["X-CSRFToken"] = token.value;
  if (key) event.detail.headers["Idempotency-Key"] = key.value;
});
document.addEventListener("htmx:beforeSwap", function (event) {
  if ([409, 422].includes(event.detail.xhr.status) &&
      (event.detail.xhr.getResponseHeader("Content-Type") || "").includes("text/html")) {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});
