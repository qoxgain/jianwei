import {describe,it,expect,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
vi.hoisted(()=>{Object.assign(globalThis,{location:{protocol:'file:',search:''}});});
import {Curve} from './RealVideoLearningPanel';
describe('recorded real-video visualizations',()=>{
 it('plots supplied numerical values with an explicit metric label',()=>{const html=renderToStaticMarkup(<Curve label="fixture MSE" series={[{name:'model',values:[3,1,2]}]}/>);expect(html).toContain('fixture MSE');expect(html).toContain('polyline');expect(html).toContain('50,20');expect(html).not.toContain('NaN');});
 it('handles absent curves without fabricated values',()=>{const html=renderToStaticMarkup(<Curve label="empty" series={[]}/>);expect(html).not.toContain('polyline');expect(html).not.toContain('NaN');expect(html).not.toContain('Infinity');});
});
