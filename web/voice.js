// Голосовые, когда сервер не умеет распознавать речь (не Мак: нет mlx-whisper).
//
// Если /healthz отвечает asr: true, этот файл ничего не делает — запись и
// файлы уходят на /run-audio, голос остаётся на машине. Иначе кнопки
// «Записать» и «Загрузить файл» подменяются копиями без старых обработчиков:
// запись распознаёт сам браузер, а файл на сервер не отправляется вовсе.
(function () {
  const rec = document.getElementById("rec");
  const file = document.getElementById("audio-file");
  const state = document.getElementById("rec-state");
  const text = document.getElementById("text");
  const box = document.getElementById("voice");
  if (!rec || !file || !state || !text || !box) return;

  fetch("/healthz").then((r) => r.json()).then((health) => {
    if (health.asr !== false) return;  // сервер распознаёт сам — оставить как есть
    takeOver();
  }).catch(() => {});

  function takeOver() {
    // Копии без обработчиков из index.html: старые слали звук на /run-audio.
    const newRec = rec.cloneNode(true);
    rec.replaceWith(newRec);
    const newFile = file.cloneNode(true);
    file.replaceWith(newFile);

    newFile.addEventListener("change", () => {
      newFile.value = "";
      state.textContent = "Файлы голосовых распознаются на Маке. Здесь продиктуйте сообщение кнопкой записи.";
    });

    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      newRec.addEventListener("click", () => {
        state.textContent = "Распознавание голоса здесь недоступно. Продиктуйте в Chrome или вставьте текст.";
      });
      return;
    }

    const note = document.createElement("div");
    note.className = "muted small";
    note.textContent = "Сейчас голос распознаёт браузер: Chrome и Edge отправляют звук в Google, Safari в Apple";
    box.after(note);

    let listening = null;
    newRec.addEventListener("click", () => {
      if (listening) { listening.stop(); return; }
      const r = new Recognition();
      r.lang = "ru-RU";
      r.interimResults = true;
      r.continuous = true;
      const before = text.value.trim();
      let finalText = "";
      r.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const piece = e.results[i][0].transcript;
          if (e.results[i].isFinal) finalText += piece; else interim += piece;
        }
        text.value = [before, (finalText + interim).trim()].filter(Boolean).join(" ");
      };
      r.onerror = (e) => {
        state.textContent = e.error === "not-allowed"
          ? "Нет доступа к микрофону — разрешите его в браузере."
          : "Не удалось распознать голос — продиктуйте ещё раз или вставьте текст.";
      };
      r.onend = () => {
        listening = null;
        newRec.textContent = "Записать";
        if (!/^(Нет доступа|Не удалось)/.test(state.textContent)) {
          state.textContent = text.value.trim() ? "Готово: проверьте текст и нажмите «Проверить»" : "";
        }
      };
      try {
        r.start();
      } catch (err) {
        state.textContent = "Не удалось включить микрофон — вставьте текст.";
        return;
      }
      listening = r;
      newRec.textContent = "Остановить";
      state.textContent = "Слушаю — говорите, текст появится в поле";
    });
  }
})();
