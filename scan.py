import re
from pathlib import Path


paths = ["./templates", "./static/js"]


def get_character():
    for path in paths:
        for file in Path(path).glob("*"):
            with open(file, "r") as f:
                content = f.read()
                for item in re.findall(
                    r"(['\"])(.*?)(\1)",
                    content,
                ):
                    if re.search(r"[\u4e00-\u9fff]", item[1]):
                        print(item)
                        yield f'"": "{item[1]}",'
                if file.suffix == ".html":
                    for item in re.findall(
                        r"(\>)(.*?)(\<)",
                        content,
                    ):
                        if re.search(r"[\u4e00-\u9fff]", item[1]):
                            print(item)
                            yield f'"": "{item[1]}",'


with open("./static/locales/zh-CN.json", "w") as w:
    w.write("{")
    w.writelines(get_character())
    w.write("}")
