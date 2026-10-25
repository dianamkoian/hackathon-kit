// Текст со скриншота: распознаём в браузере, картинка на сервер не уходит.
//
// Подключается из index.html одной строкой. Кнопка там скрыта и появляется
// только здесь: если этот файл не загрузился, на экране нет кнопки, которая
// ничего не делает. Tesseract.js грузится с CDN при первом использовании, а
// не при открытии страницы — недоступный CDN не тормозит основной экран.
(function () {
  const TESSERACT = "https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js";
  const FAIL = "Не удалось распознать, скопируйте текст вручную";

  const box = document.getElementById("shot");
  const input = document.getElementById("shot-file");
  const state = document.getElementById("shot-state");
  const text = document.getElementById("text");
  if (!box || !input || !state || !text) return;
  box.hidden = false;

  let loading = null;
  function loadTesseract() {
    if (window.Tesseract) return Promise.resolve(window.Tesseract);
    if (!loading) {
      loading = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = TESSERACT;
        s.onload = () => (window.Tesseract ? resolve(window.Tesseract) : reject(new Error("no Tesseract")));
        s.onerror = () => { loading = null; reject(new Error("cdn")); };
        document.head.appendChild(s);
      });
    }
    return loading;
  }

  let busy = false;
  async function recognize(image) {
    if (busy) return;
    busy = true;
    state.textContent = "Распознаю текст… 0%";
    let worker = null;
    try {
      const Tesseract = await loadTesseract();
      worker = await Tesseract.createWorker("rus+eng", 1, {
        logger: (m) => {
          if (m.status === "recognizing text") {
            state.textContent = `Распознаю текст… ${Math.round(m.progress * 100)}%`;
          }
        },
      });
      const { data } = await worker.recognize(image);
      const found = (data.text || "").trim();
      if (!found) throw new Error("empty");
      // Человек проверяет распознанное сам и сам жмёт «Проверить».
      text.value = found;
      text.focus();
      state.textContent = "Готово: проверьте текст и нажмите «Проверить»";
    } catch (err) {
      state.textContent = FAIL;
    } finally {
      if (worker) worker.terminate().catch(() => {});
      busy = false;
    }
  }

  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    input.value = "";
    if (file) recognize(file);
  });

  // Ctrl+V картинки прямо в поле сообщения. Обычный текст вставляется как всегда.
  text.addEventListener("paste", (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const item of items) {
      if (item.kind === "file" && item.type.startsWith("image/")) {
        e.preventDefault();
        recognize(item.getAsFile());
        return;
      }
    }
  });
})();
