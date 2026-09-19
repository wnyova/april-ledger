(() => {
  const root = document.documentElement;
  const stored = localStorage.getItem('ledger-theme');
  if (stored) root.dataset.theme = stored;
  document.getElementById('themeToggle')?.addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('ledger-theme', root.dataset.theme);
  });
  document.getElementById('menuToggle')?.addEventListener('click', () => document.getElementById('sidebar')?.classList.toggle('open'));
  setTimeout(() => document.querySelectorAll('.flash-stack .flash').forEach(el => el.classList.add('fade')), 3500);

  window.setupTransactionForm = () => {
    const type = document.getElementById('txType');
    const categoryField = document.getElementById('categoryField');
    const destinationField = document.getElementById('destinationField');
    const category = document.getElementById('categorySelect');
    if (!type || !category) return;
    const update = () => {
      const isTransfer = type.value === 'transfer';
      categoryField.classList.toggle('hidden', isTransfer);
      destinationField.classList.toggle('hidden', !isTransfer);
      category.disabled = isTransfer;
      category.required = !isTransfer && type.value !== category.dataset.uncategorizedKind;
      const destination = destinationField.querySelector('select');
      destination.disabled = !isTransfer;
      destination.required = isTransfer;
      const previousCategory = category.value;
      [...category.options].forEach(opt => {
        opt.hidden = opt.dataset.kind !== type.value;
        opt.disabled = opt.hidden;
      });
      const first = [...category.options].find(o => !o.disabled);
      const selected = [...category.options].find(o => o.value === previousCategory && !o.disabled);
      if (selected) category.value = selected.value;
      else if (first) category.value = first.value;
    };
    type.addEventListener('change', update); update();
  };

  window.setupAccountForms = () => {
    document.querySelectorAll('.accountType').forEach(type => {
      const form = type.closest('form');
      if (!form) return;
      const fields = form.querySelector('.creditFields');
      const opening = form.querySelector('.openingLabel');
      const update = () => {
        const isCredit = type.value === 'credit';
        fields?.classList.toggle('hidden', !isCredit);
        if (opening) opening.childNodes[0].textContent = isCredit ? 'Opening debt' : 'Opening balance';
      };
      type.addEventListener('change', update);
      update();
    });
  };
})();
