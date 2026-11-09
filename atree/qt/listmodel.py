"""A list model keyed by ``uid`` that updates itself by minimal diff."""
from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt


class KeyedListModel(QAbstractListModel):
    def __init__(self, roles, parent=None):
        super().__init__(parent)
        self._roles = list(roles)
        self._role_id = {name: Qt.UserRole + 1 + i for i, name in enumerate(self._roles)}
        self._items = []
        self._index = {}

    # Qt interface
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self):
        return {rid: QByteArray(name.encode()) for name, rid in self._role_id.items()}

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        if role == Qt.DisplayRole:
            role = self._role_id["uid"]
        name = self._roles[role - Qt.UserRole - 1] if Qt.UserRole < role <= Qt.UserRole + len(self._roles) else None
        return None if name is None else self._items[index.row()].get(name)

    # Python interface
    def items(self):
        return list(self._items)

    def __len__(self):
        return len(self._items)

    def _reindex(self):
        self._index = {it["uid"]: i for i, it in enumerate(self._items)}

    def _clean(self, item):
        return {name: item.get(name) for name in self._roles}

    def sync(self, new_items):
        """Make the model equal to ``new_items``; returns True if anything changed."""
        new, wanted = [], set()
        for it in new_items:
            it = self._clean(it)
            if it["uid"] not in wanted:  # a repeated uid would corrupt the moves below
                wanted.add(it["uid"])
                new.append(it)
        touched = False
        for i in range(len(self._items) - 1, -1, -1):
            if self._items[i]["uid"] not in wanted:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._items[i]
                self.endRemoveRows()
                touched = True
        self._reindex()
        for i, item in enumerate(new):
            j = self._index.get(item["uid"])
            if j is None:
                self.beginInsertRows(QModelIndex(), i, i)
                self._items.insert(i, item)
                self.endInsertRows()
                self._reindex()
                touched = True
                continue
            if j != i:
                self.beginMoveRows(QModelIndex(), j, j, QModelIndex(), i)
                self._items.insert(i, self._items.pop(j))
                self.endMoveRows()
                self._reindex()
                touched = True
            if self._update(i, item):
                touched = True
        return touched

    def _update(self, i, item):
        old = self._items[i]
        changed = [self._role_id[k] for k in self._roles if old.get(k) != item.get(k)]
        if not changed:
            return False
        self._items[i] = item
        idx = self.index(i)
        self.dataChanged.emit(idx, idx, changed)
        return True

    def set_role(self, uid, role, value):
        i = self._index.get(uid)
        if i is None or self._items[i].get(role) == value:
            return False
        self._items[i] = dict(self._items[i], **{role: value})
        idx = self.index(i)
        self.dataChanged.emit(idx, idx, [self._role_id[role]])
        return True
