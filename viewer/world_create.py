"""Creation workspace and small persistent text editors for world metadata."""
from viewer import pages
E = pages._escape


def render(store, jobs, *, from_id=None, genre_id=None, copy_mode=False, method=None):
    # Old method bookmarks enter the same coauthor workflow.
    from viewer.creation_workflow import render as workflow
    return workflow(store,jobs,selected=from_id if copy_mode else None)


def basics_editor(world_id, world_yaml, *, story=False):
    fields = [('initial_story','導入・開始時点の状況')] if story else [('name','世界の名前'),('overview','世界の概要')]
    controls=[]
    for key,label in fields:
        value=E(world_yaml.get(key) or '')
        control=(f'<input name="{key}" value="{value}" maxlength="120" required>' if key=='name' else f'<textarea name="{key}" rows="4" maxlength="8000">{value}</textarea>')
        controls.append(f'<label class="wc-field ui-field">{label}{control}</label>')
    return f'<details class="world-basics-editor"><summary>{"導入文を編集" if story else "名前・概要を編集"}</summary><form data-world-basics="{E(world_id)}">'+''.join(controls)+'<button type="submit">保存</button><p role="status" data-basics-status></p></form></details>'
