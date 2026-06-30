class TrieNode:
    def __init__(self):
        self.children = {}
        self.value = None


class Trie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, word, value):
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]

        node.value = value

    def search(self, text):
        result = []
        n = len(text)

        for i in range(n):
            node = self.root
            j = i

            while j < n and text[j] in node.children:
                node = node.children[text[j]]

                if node.value:
                    result.append(node.value)

                j += 1

        return result