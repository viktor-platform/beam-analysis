from io import StringIO
from pathlib import Path

from anastruct import SystemElements
import pandas as pd

from viktor import ViktorController, UserError
from viktor.parametrization import ViktorParametrization, OptionField, Table, Text, Tab, AutocompleteField, LineBreak, OptimizationButton, BooleanField
from viktor.result import OptimizationResultElement, OptimizationResult
from viktor.views import ImageView, ImageResult, ImageAndDataView, DataGroup, DataItem, ImageAndDataResult, DataStatus
from viktor.parametrization import NumberField


def get_profile_types(params, **kwargs):
    file_path = Path(__file__).parent / 'profiles'/f"steel-profiles-{params.input.profile_type}.csv"
    df = pd.read_csv(file_path, header=[2], skiprows=[3, 4, 5])
    return df['Profile'].values.tolist()


def get_node_id_options(params, **kwargs):
    return [str(i) for i in range(1, len(params.input.nodes) + 1)]


def get_element_id_options(params, **kwargs):
    return [str(i) for i in range(1, len(params.input.nodes))]


class Parametrization(ViktorParametrization):
    info = Tab('Info')
    info.text_01 = Text(
        """## Welcome to the beam analysis app!

This app can be used to perform 2D structural beam calculations. With this app you can:
1. Parametrically define the geometry, from a simple beam to a complex truss structure
2. Apply supports, point loads and distributed loads
3. Select a steel profile from a standard library (IPE, HEA, HEB)
4. Calculate and visualize resulting reaction forces, shear forces, bending moments and displacements
5. Optimize the required steel profile (based on bending moment)

The calculation core of the app is **anaStruct** ([docs](https://anastruct.readthedocs.io/en/latest/), 
[github](https://github.com/ritchie46/anaStruct)), a wonderful Python package created by [Ritchie Vink](https://www.ritchievink.com/).

The code behind this app is open-source available on [github](https://github.com/viktor-platform/beam-analysis-app). 
Ideas on improvements can be posted [here](https://github.com/viktor-platform/beam-analysis-app/discussions/categories/ideas).

Feel free to calculate your structure by changing the **Input** on the next tab! 

*Note that this is not a validated software package. The app can only be used for indicative calculations, 
correct results are not guaranteed.*
""")

    input = Tab('Input')
    input.nodes = Table('Nodes', default=[{'x': 0, 'y': 0}, {'x': 5, 'y': 0}, {'x': 10, 'y': 0}, {'x': 13, 'y': 0}])
    input.nodes.x = NumberField('X', suffix='m')
    input.nodes.y = NumberField('Y', suffix='m')

    input.supports = Table('Supports', default=[{'node_id': '1', 'type': 'Hinged'}, {'node_id': '3', 'type': 'Roll'}])
    input.supports.node_id = OptionField('Node ID', options=get_node_id_options)
    input.supports.type = OptionField('Type', options=['Fixed', 'Hinged', 'Roll'], variant='radio-inline')

    input.point_loads = Table('Point loads', default=[{'node_id': '2', 'fx': 0, 'fy': -15}])
    input.point_loads.node_id = OptionField('Node ID', options=get_node_id_options)
    input.point_loads.fx = NumberField('Fx', suffix='kN')
    input.point_loads.fy = NumberField('Fy', suffix='kN')

    input.distributed_loads = Table('Distributed loads', default=[{'element_id': '3', 'q': -5}])
    input.distributed_loads.element_id = OptionField('Element ID', options=get_element_id_options)
    input.distributed_loads.q = NumberField('q', suffix='kN/m')

    input.profile_type = OptionField('Profile type', options=['IPE', 'HEA', 'HEB'], default='IPE', variant='radio-inline', flex=80)
    input.nl1 = LineBreak()
    input.profile = AutocompleteField('Profile', options=get_profile_types, default='IPE240', description=
    "The source of profile properties can be found [here](https://eurocodeapplied.com/design/en1993/ipe-hea-heb-hem-design-properties)")
    input.steel_class = OptionField('Steel class', options=['S235', 'S275', 'S355'], default='S235')
    input.include_weight = BooleanField('Include weight')
    input.nl2 = LineBreak()
    input.optimize = OptimizationButton('Optimize profile', 'optimize_profile', longpoll=True, flex=35, description=
    "The optimization is based on the allowable bending moment (normal forces are not taken into account)")


class Controller(ViktorController):
    viktor_enforce_field_constraints = True  # Resolves upgrade instruction https://docs.viktor.ai/sdk/upgrades#U83

    label = 'Beam Calculator'
    parametrization = Parametrization(width=30)

    @ImageView("Structure", duration_guess=1)
    def create_structure(self, params, **kwargs):
        ss = self.create_model(params, solve_model=False)
        fig = ss.show_structure(show=False)
        return ImageResult(self.fig_to_svg(fig))

    @ImageView("Reaction forces", duration_guess=1)
    def show_reaction_forces(self, params, **kwargs):
        ss = self.create_model(params)
        fig = ss.show_reaction_force(show=False)
        return ImageResult(self.fig_to_svg(fig))

    @ImageView("Shear forces", duration_guess=1)
    def show_shear_forces(self, params, **kwargs):
        ss = self.create_model(params)
        fig = ss.show_shear_force(show=False)
        return ImageResult(self.fig_to_svg(fig))

    @ImageAndDataView("Bending moments", duration_guess=1)
    def show_bending_moments(self, params, **kwargs):
        ss = self.create_model(params)
        fig = ss.show_bending_moment(show=False)

        max_moment = abs(max(ss.get_element_result_range('moment'), key=abs))
        results = self.calculate_allowable_bending_moment(params.input.profile_type, params.input.profile, params.input.steel_class)
        uc = abs(max_moment/results['allowable_bending_moment'])

        if uc < 1:
            status = DataStatus.SUCCESS
            status_msg = ''
        else:
            status = DataStatus.ERROR
            status_msg = 'UC should not exceed 1.0'

        data = DataGroup(
            DataItem('Maximum bending moment', max_moment, suffix='kNm', number_of_decimals=0),
            DataItem('Allowable bending moment', results['allowable_bending_moment'], suffix='kNm', number_of_decimals=0,
                     subgroup=DataGroup(
                         DataItem('Yield strength', results['yield_strength'], suffix='MPa', number_of_decimals=0),
                         DataItem('Second moment of area (Iy)', results['moment_of_inertia'], suffix='x 10^6 mm4'),
                         DataItem('Profile height', results['profile_height'], suffix='mm')
                     )),
            DataItem('UC', uc, number_of_decimals=2, status=status, status_message=status_msg)
        )

        return ImageAndDataResult(self.fig_to_svg(fig), data)

    @ImageView("Displacements", duration_guess=1)
    def show_displacements(self, params, **kwargs):
        ss = self.create_model(params)
        fig = ss.show_displacement(show=False)
        return ImageResult(self.fig_to_svg(fig))

    def optimize_profile(self, params, **kwargs):
        profile_type = params.input.profile_type
        steel_class = params.input.steel_class

        profiles = get_profile_types(params)
        results = []
        for profile in profiles:
            params['input']['profile'] = profile
            ss = self.create_model(params)
            max_moment = abs(max(ss.get_element_result_range('moment'), key=abs))
            allowable_moment = self.calculate_allowable_bending_moment(profile_type, profile, steel_class)['allowable_bending_moment']
            uc = abs(max_moment / allowable_moment)

            if uc < 1:
                results.append(OptimizationResultElement({'input': {'profile': profile}}, {'uc': round(uc, 2)}))

        output_headers = {'uc': 'UC'}
        return OptimizationResult(results, ['input.profile'], output_headers=output_headers)

    def create_model(self, params, solve_model=True):
        youngs_modulus = 210000 * 10**3  # kN/m2
        profile_type = params.input.profile_type
        profile = params.input.profile
        moment_of_inertia = self.get_profile_property(profile_type, profile, 'Second moment of area') / 10**6  # Convert x10^6 mm4 to m4
        ss = SystemElements(EI=youngs_modulus * moment_of_inertia)

        if params.input.include_weight:
            weight = self.get_profile_property(profile_type, profile, 'Weight') * 9.81 / 1000  # Convert kg/m to kN/m
        else:
            weight = 0

        # Create elements
        nodes = params.input.nodes
        for i, node in enumerate(nodes[:-1]):
            ss.add_element(location=[[node.x, node.y], [nodes[i+1].x, nodes[i+1].y]], g=weight)

        # Create supports
        for support in params.input.supports:
            if support.type == 'Fixed':
                ss.add_support_fixed(node_id=int(support.node_id))
            elif support.type == 'Hinged':
                ss.add_support_hinged(node_id=int(support.node_id))
            elif support.type == 'Roll':
                ss.add_support_roll(node_id=int(support.node_id), direction=2)

        # Create point loads
        for point_load in params.input.point_loads:
            ss.point_load(node_id=int(point_load.node_id), Fx=point_load.fx, Fy=point_load.fy)

        # Create distributed loads
        for distributed_load in params.input.distributed_loads:
            ss.q_load(q=distributed_load.q, element_id=int(distributed_load.element_id), direction='element')

        # Solve the model
        if solve_model:
            try:
                ss.solve()
            except:
                raise UserError("Calculation cannot be solved, probably because the structure is instable. Check the supports.")

        return ss

    @staticmethod
    def fig_to_svg(fig):
        svg_data = StringIO()
        fig.savefig(svg_data, format='svg')
        return svg_data

    @staticmethod
    def get_profile_property(profile_type, profile, name):
        file_path = Path(__file__).parent / 'profiles' / f"steel-profiles-{profile_type}.csv"
        df = pd.read_csv(file_path, header=[2], skiprows=[3, 4, 5])
        return df.loc[df['Profile'] == profile, name].item()

    @staticmethod
    def calculate_allowable_bending_moment(profile_type, profile, steel_class):
        file_path = Path(__file__).parent / 'profiles' / f"steel-profiles-{profile_type}.csv"
        df = pd.read_csv(file_path, header=[2], skiprows=[3, 4, 5])
        moment_of_inertia = df.loc[df['Profile'] == profile, 'Second moment of area'].item()
        profile_height = df.loc[df['Profile'] == profile, 'Depth'].item()

        yield_strength = float(steel_class[-3:])  # Yield strength is based on the steel class, i.e. the yields strength of S235 is 235MPa
        allowable_bending_moment = (yield_strength * moment_of_inertia) / (profile_height/2)
        return {'moment_of_inertia': moment_of_inertia, 'profile_height': profile_height,
                'yield_strength': yield_strength, 'allowable_bending_moment': allowable_bending_moment}
