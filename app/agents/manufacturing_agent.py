"""Manufacturing documentation specialist delegates to the existing deterministic engines."""
from app.manufacturing import schedules_from_registry, export_manufacturing
from app.drawings import export_drawings


class ManufacturingDocumentationAgent:
    def __init__(self,specification,build):
        self.specification,self.build = specification,build
        self.schedules = schedules_from_registry(build,specification)

    def generate_cut_list(self): return self.schedules['parts']
    def generate_material_bom(self): return self.schedules['bom']
    def generate_hardware_schedule(self): return self.schedules['hardware']
    def generate_glass_schedule(self): return [r for r in self.schedules['parts'] if r['material']=='glass']
    def generate_shop_drawings(self,output):
        # Existing drawing engine emits explicitly labelled review drawings.
        return export_drawings(self.specification,self.build,output)
    def export_documentation(self,output,report):
        return export_manufacturing(self.specification,self.build,output,report)
