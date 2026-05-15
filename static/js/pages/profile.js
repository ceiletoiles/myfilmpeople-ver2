document.addEventListener("DOMContentLoaded", () => {
	const storageKey = "myfilmpeople.followingTab";
	const tabs = Array.from(document.querySelectorAll('input[name="following-tab"]'));
	const statusMenus = Array.from(document.querySelectorAll(".status-filter-menu"));
	if (tabs.length === 0) return;

	const setActiveTab = (tabId) => {
		if (!tabId) return;
		const target = tabs.find((tab) => tab.id === tabId);
		if (target) {
			target.checked = true;
		}
	};

	const savedTabId = window.sessionStorage.getItem(storageKey);
	if (savedTabId) {
		setActiveTab(savedTabId);
	}

	tabs.forEach((tab) => {
		tab.addEventListener("change", () => {
			if (tab.checked) {
				window.sessionStorage.setItem(storageKey, tab.id);
			}
		});
	});

	const checkedTab = tabs.find((tab) => tab.checked);
	if (checkedTab) {
		window.sessionStorage.setItem(storageKey, checkedTab.id);
	}

	const closeStatusMenus = () => {
		statusMenus.forEach((menu) => {
			menu.open = false;
		});
	};

	document.addEventListener("pointerdown", (event) => {
		if (statusMenus.length === 0) return;
		const target = event.target instanceof Node ? event.target : null;
		if (!target) return;
		const clickedInsideMenu = statusMenus.some((menu) => menu.contains(target));
		if (!clickedInsideMenu) {
			closeStatusMenus();
		}
	});

	document.addEventListener("keydown", (event) => {
		if (event.key === "Escape") {
			closeStatusMenus();
		}
	});
});