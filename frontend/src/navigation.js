const tabs=new Set(['reference','model','manufacturing']);

export function readStudioLocation(location=window.location){
  const params=new URLSearchParams(location.search);
  const tab=params.get('tab')==='renders'?'model':params.get('tab');
  return {project:params.get('project')||'',tab:tabs.has(tab)?tab:'reference'};
}

export function saveStudioLocation(project,tab){
  const url=new URL(window.location.href);if(project)url.searchParams.set('project',project);else url.searchParams.delete('project');url.searchParams.set('tab',tab);
  url.searchParams.delete('model');
  window.history.replaceState(null,'',url);
}
