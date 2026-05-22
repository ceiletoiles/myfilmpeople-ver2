document.addEventListener("DOMContentLoaded", () => {
	const storageKey = "myfilmpeople.followingTab";
	const tabs = Array.from(document.querySelectorAll('input[name="following-tab"]'));
	const statusMenus = Array.from(document.querySelectorAll(".status-filter-menu"));
	const followingCountEl = document.querySelector("[data-following-count]");
	if (tabs.length === 0) return;

	const setActiveTab = (tabId) => {
		if (!tabId) return;
		const target = tabs.find((tab) => tab.id === tabId);
		if (target) {
			target.checked = true;
		}
	};

	const updateFollowingCount = () => {
		if (!followingCountEl) return;
		const checked = tabs.find((tab) => tab.checked) || tabs[0];
		if (!checked) return;
		const label = document.querySelector(`label[for="${checked.id}"]`);
		const countText = (label?.dataset.tabCount || "0").trim();
		followingCountEl.textContent = countText === "0" ? "" : countText;
	};

	const savedTabId = window.sessionStorage.getItem(storageKey);
	if (savedTabId) {
		setActiveTab(savedTabId);
	}

	tabs.forEach((tab) => {
		tab.addEventListener("change", () => {
			if (tab.checked) {
				window.sessionStorage.setItem(storageKey, tab.id);
				updateFollowingCount();
			}
		});
	});

	const checkedTab = tabs.find((tab) => tab.checked);
	if (checkedTab) {
		window.sessionStorage.setItem(storageKey, checkedTab.id);
	}

	updateFollowingCount();

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