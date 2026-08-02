(function () {
    'use strict';

    const STORAGE_KEY = 'cricket-ui-language';
    const dictionaries = {
        en: {},
        ru: {
            'Cricket RTMP Node — Monitoring': 'Cricket RTMP Node — Мониторинг',
            'Stream Configuration': 'Конфигурация трансляций',
            'Restream Control Panel': 'Управление рестримами',
            'Monitoring': 'Мониторинг',
            'Configuration': 'Конфигурация',
            'Restreams': 'Рестримы',
            'Live streams': 'Прямые трансляции',
            'Field monitoring · India & Pakistan': 'Мониторинг площадок · Индия и Пакистан',
            'System online': 'Система работает',
            'Columns': 'Колонки',
            'Loading streams…': 'Загрузка трансляций…',
            'Stream Configuration': 'Конфигурация трансляций',
            'Configure RTMP ingest slots and publishing settings': 'Настройка RTMP-площадок и параметров публикации',
            'Total fields': 'Всего площадок',
            'Enabled': 'Включено',
            'Active now': 'Активно сейчас',
            'Add New Field': 'Добавить площадку',
            'Field Name:': 'Название площадки:',
            'Emoji:': 'Значок:',
            'Stream Key:': 'Ключ потока:',
            'Enabled:': 'Включена:',
            'Cancel': 'Отмена',
            'Save': 'Сохранить',
            'Restream Control Panel': 'Управление рестримами',
            'Manage outgoing RTMP destinations': 'Управление исходящими RTMP-направлениями',
            'Publish settings': 'Параметры публикации',
            'Technical information': 'Технические параметры',
            'RTMP URL': 'RTMP URL',
            'Stream key': 'Ключ потока',
            'Protected Stream Key:': 'Защищённый ключ потока:',
            'Slot:': 'Слот:',
            'Edit': 'Изменить',
            'Disable': 'Выключить',
            'Enable': 'Включить',
            'Delete': 'Удалить',
            'Active': 'Активно',
            'Stale': 'Устарело',
            'No signal': 'Нет сигнала',
            'Live': 'В эфире',
            'Offline': 'Не в эфире',
            'Publish authentication:': 'Авторизация публикации:',
            'Protected stream key:': 'Защищённый ключ потока:',
            'Secret Key:': 'Секретный ключ:',
            'Authentication is enforced by Nginx': 'Авторизация проверяется Nginx',
            'Authentication is not enabled for this slot': 'Авторизация для этой площадки не включена',
            'No streams enabled. Configure streams at Configuration.': 'Нет включённых трансляций. Настройте их на странице «Конфигурация».',
            'Resolution': 'Разрешение',
            'Source FPS': 'FPS источника',
            'Input bitrate': 'Входной битрейт',
            'Video codec': 'Видеокодек',
            'Audio codec': 'Аудиокодек',
            'Uptime': 'Время работы',
            'RTMP dropped': 'Потери RTMP',
            'Latest media': 'Последние данные',
            'HLS segment': 'Сегмент HLS',
            'HLS latency': 'Задержка HLS',
            'Player buffer': 'Буфер плеера',
            'Browser dropped': 'Пропущено браузером',
            'Start': 'Запустить',
            'Stop': 'Остановить',
            'Destination': 'Направление',
            'Destinations': 'Направления',
            'No destinations configured': 'Направления не настроены',
            'Loading…': 'Загрузка…',
            'Refresh': 'Обновить'
        }
    };

    function normalizeLanguage(value) {
        return value === 'ru' ? 'ru' : 'en';
    }

    function currentLanguage() {
        return normalizeLanguage(localStorage.getItem(STORAGE_KEY));
    }

    function translateText(value, language = currentLanguage()) {
        if (language === 'en') return value;
        const leading = value.match(/^\s*/)[0];
        const trailing = value.match(/\s*$/)[0];
        const text = value.trim();
        if (!text) return value;
        const translated = dictionaries.ru[text];
        if (translated) return `${leading}${translated}${trailing}`;
        const decorated = text.match(/^([^A-Za-zА-Яа-я0-9]*)(.*)$/u);
        if (decorated && dictionaries.ru[decorated[2]]) {
            return `${leading}${decorated[1]}${dictionaries.ru[decorated[2]]}${trailing}`;
        }
        return value;
    }

    function translateElement(root = document) {
        const language = currentLanguage();
        document.documentElement.lang = language;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        nodes.forEach(node => {
            const parent = node.parentElement;
            if (!parent || ['SCRIPT', 'STYLE', 'CODE'].includes(parent.tagName)) return;
            if (!node.__cricketEnglish) node.__cricketEnglish = node.nodeValue;
            const translatedValue = language === 'ru'
                ? translateText(node.__cricketEnglish, language)
                : node.__cricketEnglish;
            if (node.nodeValue !== translatedValue) node.nodeValue = translatedValue;
        });
        document.querySelectorAll('[placeholder], [title]').forEach(element => {
            ['placeholder', 'title'].forEach(attribute => {
                if (!element.hasAttribute(attribute)) return;
                const property = `cricketEnglish${attribute}`;
                if (!element.dataset[property]) {
                    element.dataset[property] = element.getAttribute(attribute);
                }
                const source = element.dataset[property];
                const translatedValue = language === 'ru' ? translateText(source, language) : source;
                if (element.getAttribute(attribute) !== translatedValue) {
                    element.setAttribute(attribute, translatedValue);
                }
            });
        });
        updateSwitcher();
    }

    function setLanguage(language) {
        localStorage.setItem(STORAGE_KEY, normalizeLanguage(language));
        translateElement(document.body);
        window.dispatchEvent(new CustomEvent('cricket-language-change', {
            detail: {language: currentLanguage()}
        }));
    }

    function updateSwitcher() {
        const language = currentLanguage();
        document.querySelectorAll('.language-switcher button').forEach(button => {
            button.classList.toggle('active', button.dataset.language === language);
            button.setAttribute('aria-pressed', String(button.dataset.language === language));
        });
    }

    function mountSwitcher() {
        if (document.querySelector('.language-switcher')) return;
        const host = document.querySelector('.heading-actions')
            || document.querySelector('.topbar')
            || document.body;
        const switcher = document.createElement('div');
        switcher.className = 'language-switcher';
        switcher.setAttribute('aria-label', 'Language');
        switcher.innerHTML = '<button type="button" data-language="en">EN</button><button type="button" data-language="ru">RU</button>';
        switcher.addEventListener('click', event => {
            const button = event.target.closest('button[data-language]');
            if (button) setLanguage(button.dataset.language);
        });
        host.prepend(switcher);
        updateSwitcher();
    }

    let scheduled = false;
    const observer = new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => {
            scheduled = false;
            translateElement(document.body);
        });
    });

    window.CricketI18n = {currentLanguage, setLanguage, translateElement};
    document.addEventListener('DOMContentLoaded', () => {
        mountSwitcher();
        translateElement(document.body);
        observer.observe(document.body, {childList: true, subtree: true});
    });
}());
